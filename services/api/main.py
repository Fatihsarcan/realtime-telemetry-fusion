"""Yayin katmani: REST (gecmis sorgu) + WebSocket (canli akis).

Anlik goruntu Redis'ten, gecmis PostgreSQL'den okunur. Boylece canli harita
sorgulari veritabanina hic dokunmaz.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import asyncpg
import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from telemetry_common import get_logger, settings

log = get_logger("api")
WEB_DIR = Path(__file__).parent / "web"

state: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    state["redis"] = aioredis.from_url(settings.redis_url, decode_responses=True)
    state["pool"] = await _create_pool()
    state["hub"] = LiveHub(state["redis"])
    await state["hub"].start()
    log.info("api hazir")
    yield
    await state["hub"].stop()
    await state["pool"].close()
    await state["redis"].aclose()


async def _create_pool(attempts: int = 30, delay: float = 2.0) -> asyncpg.Pool:
    last: Exception | None = None
    for _ in range(attempts):
        try:
            return await asyncpg.create_pool(settings.postgres_dsn, min_size=1, max_size=5)
        except Exception as exc:  # noqa: BLE001
            last = exc
            await asyncio.sleep(delay)
    raise RuntimeError(f"PostgreSQL'e baglanilamadi: {last}")


class LiveHub:
    """Tek Redis aboneligini tum WebSocket istemcilerine dagitir.

    Her istemci icin ayri abonelik acmak Redis baglantisini gereksiz mesgul eder;
    tek dinleyici + bellek ici fanout yuzlerce istemcide de sabit maliyetli kalir.
    """

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis
        self._clients: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._listen())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def subscribe(self) -> asyncio.Queue:
        # Yavas istemci tum sistemi yavaslatmasin: kuyruk dolarsa mesaj dusurulur
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._clients.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._clients.discard(queue)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def _listen(self) -> None:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(settings.live_channel)
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                data = message["data"]
                for queue in list(self._clients):
                    try:
                        queue.put_nowait(data)
                    except asyncio.QueueFull:
                        pass
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe(settings.live_channel)
                await pubsub.aclose()


app = FastAPI(
    title="Telemetry Fusion API",
    version="0.1.0",
    description="Coklu kaynakli telemetri fuzyon platformu - canli takip ve gecmis sorgu",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, Any]:
    """Bagimliliklarin gercekten cevap verdigini dogrular (deploy saglik kontrolu)."""
    checks = {}
    try:
        await state["redis"].ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"hata: {exc}"
    try:
        async with state["pool"].acquire() as conn:
            await conn.fetchval("SELECT 1")
        checks["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["postgres"] = f"hata: {exc}"
    healthy = all(v == "ok" for v in checks.values())
    return {"status": "ok" if healthy else "degraded", "checks": checks}


@app.get("/api/tracks")
async def list_tracks(
    source: str | None = Query(None, description="Kaynak filtresi, or. opensky"),
    bbox: str | None = Query(None, description="lat_min,lat_max,lon_min,lon_max"),
    limit: int = Query(2000, ge=1, le=10000),
) -> dict[str, Any]:
    """Anlik goruntu: tum nesnelerin son bilinen konumu (Redis'ten)."""
    redis: aioredis.Redis = state["redis"]
    pattern = f"track:{source}:*" if source else "track:*"

    keys: list[str] = []
    async for key in redis.scan_iter(match=pattern, count=1000):
        keys.append(key)
        if len(keys) >= limit * 2:
            break
    if not keys:
        return {"count": 0, "tracks": []}

    bounds = _parse_bbox(bbox)
    tracks = []
    for raw in await redis.mget(keys):
        if not raw:
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if bounds and not _within(item, bounds):
            continue
        tracks.append(item)
        if len(tracks) >= limit:
            break
    return {"count": len(tracks), "tracks": tracks}


@app.get("/api/tracks/{source}/{source_id}/history")
async def track_history(
    source: str,
    source_id: str,
    minutes: int = Query(60, ge=1, le=1440),
    limit: int = Query(1000, ge=1, le=10000),
) -> dict[str, Any]:
    """Bir nesnenin gecmis rotasi (PostgreSQL'den)."""
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    async with state["pool"].acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ts, lat, lon, altitude_m, speed_mps, heading_deg, on_ground
            FROM observations
            WHERE source = $1 AND source_id = $2 AND ts >= $3
            ORDER BY ts ASC
            LIMIT $4
            """,
            source, source_id, since, limit,
        )
    if not rows:
        raise HTTPException(status_code=404, detail="Bu nesne icin verilen aralikta kayit yok")
    return {
        "source": source,
        "source_id": source_id,
        "count": len(rows),
        "points": [{**dict(r), "ts": r["ts"].isoformat()} for r in rows],
    }


@app.get("/api/stats")
async def stats() -> dict[str, Any]:
    """Pipeline saglik metrikleri: hacim, gecikme (p50/p95), aktif nesne sayisi."""
    redis: aioredis.Redis = state["redis"]
    raw_stats = await redis.hgetall("stats:processor")
    samples = [float(v) for v in await redis.lrange("stats:batch_ms", 0, -1)]

    async with state["pool"].acquire() as conn:
        db = await conn.fetchrow(
            """
            SELECT
              (SELECT COUNT(*) FROM observations) AS observations,
              (SELECT COUNT(*) FROM tracks) AS tracks,
              (SELECT COUNT(*) FROM observations WHERE ts > NOW() - INTERVAL '1 minute') AS last_minute
            """
        )

    return {
        "pipeline": {
            "observations_written": int(raw_stats.get("observations_written", 0)),
            "duplicates_dropped": int(raw_stats.get("duplicates_dropped", 0)),
            "batches": int(raw_stats.get("batches", 0)),
            "last_batch_ms": float(raw_stats.get("last_batch_ms", 0) or 0),
        },
        "batch_latency_ms": {
            "samples": len(samples),
            "p50": _percentile(samples, 50),
            "p95": _percentile(samples, 95),
            "max": round(max(samples), 1) if samples else 0.0,
        },
        "database": {
            "observations_total": db["observations"],
            "tracks_total": db["tracks"],
            "observations_last_minute": db["last_minute"],
        },
        "live_clients": state["hub"].client_count,
    }


@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket) -> None:
    """Her yeni gozlemi anlik olarak istemciye iter."""
    await websocket.accept()
    hub: LiveHub = state["hub"]
    queue = hub.subscribe()
    try:
        while True:
            payload = await queue.get()
            await websocket.send_text(payload)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.warning("websocket hatasi", extra={"fields": {"error": str(exc)}})
    finally:
        hub.unsubscribe(queue)


def _parse_bbox(bbox: str | None) -> tuple[float, float, float, float] | None:
    if not bbox:
        return None
    try:
        lat_min, lat_max, lon_min, lon_max = (float(p) for p in bbox.split(","))
        return lat_min, lat_max, lon_min, lon_max
    except ValueError:
        raise HTTPException(status_code=400, detail="bbox bicimi: lat_min,lat_max,lon_min,lon_max") from None


def _within(item: dict[str, Any], bounds: tuple[float, float, float, float]) -> bool:
    lat_min, lat_max, lon_min, lon_max = bounds
    lat, lon = item.get("lat"), item.get("lon")
    return lat is not None and lon is not None and lat_min <= lat <= lat_max and lon_min <= lon <= lon_max


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(int(round(pct / 100 * (len(ordered) - 1))), len(ordered) - 1)
    return round(ordered[index], 1)


if WEB_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")
