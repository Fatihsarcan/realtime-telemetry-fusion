"""Kalici katman: PostgreSQL yazma islemleri."""

from __future__ import annotations

import asyncio

import asyncpg

from telemetry_common import Observation, Settings, get_logger

log = get_logger("processor.store")

INSERT_OBSERVATIONS = """
INSERT INTO observations (
    source, source_id, object_type, ts, lat, lon, altitude_m, speed_mps,
    heading_deg, vertical_rate_mps, label, country, on_ground, ingested_at
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
ON CONFLICT (source, source_id, ts) DO NOTHING
"""

UPSERT_TRACK = """
INSERT INTO tracks (
    source, source_id, object_type, last_ts, lat, lon, altitude_m, speed_mps,
    heading_deg, label, country, on_ground, sample_count, updated_at
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,1,NOW())
ON CONFLICT (source, source_id) DO UPDATE SET
    object_type  = EXCLUDED.object_type,
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

# Mevcut kurulumlari yeni semaya tasir. Hepsi idempotent: her acilista
# guvenle calisir, migration aracina gerek birakmaz.
SCHEMA_UPGRADES = [
    "ALTER TABLE observations ADD COLUMN IF NOT EXISTS object_type TEXT NOT NULL DEFAULT 'unknown'",
    "ALTER TABLE tracks ADD COLUMN IF NOT EXISTS object_type TEXT NOT NULL DEFAULT 'unknown'",
    "CREATE INDEX IF NOT EXISTS tracks_type_idx ON tracks (object_type, last_ts DESC)",
    """
    CREATE TABLE IF NOT EXISTS correlations (
        id             BIGSERIAL PRIMARY KEY,
        ts             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        a_source       TEXT NOT NULL,
        a_source_id    TEXT NOT NULL,
        a_object_type  TEXT NOT NULL,
        a_label        TEXT,
        b_source       TEXT NOT NULL,
        b_source_id    TEXT NOT NULL,
        b_object_type  TEXT NOT NULL,
        b_label        TEXT,
        distance_km    DOUBLE PRECISION NOT NULL,
        CONSTRAINT correlations_unique_pair UNIQUE (ts, a_source, a_source_id, b_source, b_source_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS correlations_ts_idx ON correlations (ts DESC)",
    "CREATE INDEX IF NOT EXISTS correlations_a_idx ON correlations (a_source, a_source_id, ts DESC)",
]

INSERT_CORRELATIONS = """
INSERT INTO correlations (
    ts, a_source, a_source_id, a_object_type, a_label,
    b_source, b_source_id, b_object_type, b_label, distance_km
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
ON CONFLICT ON CONSTRAINT correlations_unique_pair DO NOTHING
"""


async def ensure_schema(pool: asyncpg.Pool) -> None:
    """Semanin guncel oldugundan emin olur (yeni kolonlar, yeni tablolar)."""
    async with pool.acquire() as conn:
        for statement in SCHEMA_UPGRADES:
            await conn.execute(statement)
    log.info("sema dogrulandi")


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
        o.source, o.source_id, o.object_type, o.ts, o.lat, o.lon, o.altitude_m, o.speed_mps,
        o.heading_deg, o.vertical_rate_mps, o.label, o.country, o.on_ground, o.ingested_at,
    )


def _track_row(o: Observation) -> tuple:
    return (
        o.source, o.source_id, o.object_type, o.ts, o.lat, o.lon, o.altitude_m,
        o.speed_mps, o.heading_deg, o.label, o.country, o.on_ground,
    )


async def load_recent_tracks(pool: asyncpg.Pool, object_type: str, max_age_s: int) -> list[dict]:
    """Korelasyona girecek taze kayitlari getirir.

    Eski kayitlari disarida birakmak sart: kapsama disina cikmis bir nesnenin
    son bilinen konumuyla eslesme uretmek yanlis sonuc olurdu.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT source, source_id, object_type, label, lat, lon, altitude_m, last_ts
            FROM tracks
            WHERE object_type = $1
              AND last_ts > NOW() - make_interval(secs => $2)
              AND NOT on_ground
            """,
            object_type, max_age_s,
        )
    return [dict(r) for r in rows]


async def write_correlations(pool: asyncpg.Pool, correlations: list) -> int:
    """Eslesmeleri kaydeder. Ayni cift ayni saniyede iki kez yazilmaz."""
    if not correlations:
        return 0
    rows = [
        (
            c.a["last_ts"], c.a["source"], c.a["source_id"], c.a["object_type"], c.a["label"],
            c.b["source"], c.b["source_id"], c.b["object_type"], c.b["label"], c.distance_km,
        )
        for c in correlations
    ]
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.executemany(INSERT_CORRELATIONS, rows)
    return len(rows)


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
