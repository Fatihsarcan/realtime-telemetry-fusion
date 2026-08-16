"""Kalici katman: PostgreSQL yazma islemleri."""

from __future__ import annotations

import asyncio

import asyncpg

from telemetry_common import Observation, Settings, get_logger

log = get_logger("processor.store")

INSERT_OBSERVATIONS = """
INSERT INTO observations (
    source, source_id, ts, lat, lon, altitude_m, speed_mps,
    heading_deg, vertical_rate_mps, label, country, on_ground, ingested_at
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
ON CONFLICT (source, source_id, ts) DO NOTHING
"""

UPSERT_TRACK = """
INSERT INTO tracks (
    source, source_id, last_ts, lat, lon, altitude_m, speed_mps,
    heading_deg, label, country, on_ground, sample_count, updated_at
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,1,NOW())
ON CONFLICT (source, source_id) DO UPDATE SET
    last_ts      = EXCLUDED.last_ts,
    lat          = EXCLUDED.lat,
    lon          = EXCLUDED.lon,
    altitude_m   = EXCLUDED.altitude_m,
    speed_mps    = EXCLUDED.speed_mps,
    heading_deg  = EXCLUDED.heading_deg,
    label        = COALESCE(EXCLUDED.label, tracks.label),
    country      = COALESCE(EXCLUDED.country, tracks.country),
    on_ground    = EXCLUDED.on_ground,
    sample_count = tracks.sample_count + 1,
    updated_at   = NOW()
-- Gec gelen (out-of-order) paket eski durumu ustune yazmasin
WHERE EXCLUDED.last_ts > tracks.last_ts
"""


async def create_pool(cfg: Settings, attempts: int = 30, delay: float = 2.0) -> asyncpg.Pool:
    last: Exception | None = None
    for i in range(1, attempts + 1):
        try:
            return await asyncpg.create_pool(cfg.postgres_dsn, min_size=1, max_size=5, command_timeout=30)
        except Exception as exc:  # noqa: BLE001
            last = exc
            log.warning("postgres bekleniyor", extra={"fields": {"attempt": i}})
            await asyncio.sleep(delay)
    raise RuntimeError(f"PostgreSQL'e baglanilamadi: {last}")


def _obs_row(o: Observation) -> tuple:
    return (
        o.source, o.source_id, o.ts, o.lat, o.lon, o.altitude_m, o.speed_mps,
        o.heading_deg, o.vertical_rate_mps, o.label, o.country, o.on_ground, o.ingested_at,
    )


def _track_row(o: Observation) -> tuple:
    return (
        o.source, o.source_id, o.ts, o.lat, o.lon, o.altitude_m,
        o.speed_mps, o.heading_deg, o.label, o.country, o.on_ground,
    )


async def purge_old(pool: asyncpg.Pool, retention_days: int) -> int:
    """Saklama suresini asan gozlemleri siler.

    Disk sabit ve sinirli. Temizlik olmazsa tablo suresiz buyur, disk dolar ve
    sistem yazamaz hale gelir. Silme parcali yapilir; tek seferde milyonlarca
    satir silmek tabloyu uzun sure kilitler.
    """
    chunk = 10000
    total = 0
    async with pool.acquire() as conn:
        while True:
            status = await conn.execute(
                """
                DELETE FROM observations
                WHERE id IN (
                    SELECT id FROM observations
                    WHERE ts < NOW() - make_interval(days => $1)
                    LIMIT $2
                )
                """,
                retention_days, chunk,
            )
            deleted = int(status.split()[-1])
            total += deleted
            if deleted < chunk:
                break
    return total


async def write_batch(pool: asyncpg.Pool, observations: list[Observation]) -> None:
    """Gozlemleri ve turev track durumunu tek transaction icinde yazar."""
    if not observations:
        return
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.executemany(INSERT_OBSERVATIONS, [_obs_row(o) for o in observations])
            # Ayni nesnenin batch icindeki en yeni kaydi yeterli
            latest: dict[str, Observation] = {}
            for o in observations:
                current = latest.get(o.key)
                if current is None or o.ts > current.ts:
                    latest[o.key] = o
            await conn.executemany(UPSERT_TRACK, [_track_row(o) for o in latest.values()])
