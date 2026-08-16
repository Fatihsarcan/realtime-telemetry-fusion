"""Yayin katmani: REST (gecmis sorgu) + WebSocket (canli akis).

Anlik goruntu Redis'ten, gecmis PostgreSQL'den okunur. Boylece canli harita
sorgulari veritabanina hic dokunmaz.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections import Counter
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
        self._per_ip: Counter[str] = Counter()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._listen())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def subscribe(self, client_ip: str) -> asyncio.Queue | None:
        """Sinirlar icindeyse abonelik acar, degilse None doner.

        Sinirsiz baglanti kabul etmek, hatali bir istemci dongusunun veya bot
        trafiginin bellegi ve giden trafigi tuketmesine yol acar.
        """
        if len(self._clients) >= settings.max_ws_clients:
            return None
        if self._per_ip[client_ip] >= settings.max_ws_per_ip:
            return None

        # Yavas istemci tum sistemi yavaslatmasin: kuyruk dolarsa mesaj dusurulur
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        queue.__dict__["client_ip"] = client_ip
        self._clients.add(queue)
        self._per_ip[client_ip] += 1
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        if queue not in self._clients:
            return
        self._clients.discard(queue)
        client_ip = queue.__dict__.get("client_ip")
        if client_ip and self._per_ip[client_ip] > 0:
            self._per_ip[client_ip] -= 1
            if self._per_ip[client_ip] == 0:
                del self._per_ip[client_ip]

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
    source: str | None = Query(None, description="Kaynak filtresi, or. opensky, celestrak"),
    object_type: str | None = Query(None, description="Tur filtresi: aircraft veya satellite"),
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
        if object_type and item.get("object_type") != object_type:
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


@app.get("/api/correlations")
async def correlations(
    minutes: int = Query(10, ge=1, le=1440),
    limit: int = Query(200, ge=1, le=2000),
    source_id: str | None = Query(None, description="Yalnizca bu nesneyle ilgili eslesmeler"),
) -> dict[str, Any]:
    """Fuzyon ciktisi: kaynaklar arasi uzamsal eslesmeler.

    Ornek: bir uydunun yer izdusumunun bir ucaga verilen yaricap icinde
    yaklastigi anlar - kapsama analizi.
    """
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    query = """
        SELECT ts, a_source, a_source_id, a_object_type, a_label,
               b_source, b_source_id, b_object_type, b_label, distance_km
        FROM correlations
        WHERE ts >= $1
    """
    params: list[Any] = [since]
    if source_id:
        query += " AND (a_source_id = $2 OR b_source_id = $2)"
        params.append(source_id)
    query += f" ORDER BY ts DESC, distance_km ASC LIMIT ${len(params) + 1}"
    params.append(limit)

    async with state["pool"].acquire() as conn:
        rows = await conn.fetch(query, *params)

    return {
        "count": len(rows),
        "window_minutes": minutes,
        "correlations": [{**dict(r), "ts": r["ts"].isoformat()} for r in rows],
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
              (SELECT COUNT(*) FROM observations WHERE ts > NOW() - INTERVAL '1 minute') AS last_minute,
              (SELECT COUNT(*) FROM tracks WHERE object_type = 'aircraft') AS aircraft,
              (SELECT COUNT(*) FROM tracks WHERE object_type = 'satellite') AS satellites,
              (SELECT COUNT(*) FROM correlations WHERE ts > NOW() - INTERVAL '10 minutes') AS correlations_10m
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
            "aircraft_tracked": db["aircraft"],
            "satellites_tracked": db["satellites"],
        },
        "fusion": {
            "correlations_last_10min": db["correlations_10m"],
            "correlations_total": int(raw_stats.get("correlations_total", 0)),
            "last_run_matches": int(raw_stats.get("correlations_last_run", 0)),
            "last_run_ms": float(raw_stats.get("correlation_ms", 0) or 0),
            "radius_km": settings.correlation_radius_km,
        },
        "live_clients": state["hub"].client_count,
        "limits": {
            "max_ws_clients": settings.max_ws_clients,
            "max_ws_per_ip": settings.max_ws_per_ip,
            "retention_days": settings.retention_days,
        },
    }


@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket) -> None:
    """Her yeni gozlemi anlik olarak istemciye iter."""
    hub: LiveHub = state["hub"]
    client_ip = _client_ip(websocket)
    queue = hub.subscribe(client_ip)
    if queue is None:
        # 1013 = Try Again Later
        await websocket.close(code=1013, reason="Baglanti siniri doldu")
        return

    await websocket.accept()
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


def _client_ip(websocket: WebSocket) -> str:
    """Caddy arkasinda gercek istemci IP'si X-Real-IP baslindan gelir."""
    forwarded = websocket.headers.get("x-real-ip") or websocket.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return websocket.client.host if websocket.client else "bilinmiyor"


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
