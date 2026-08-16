"""Isleme katmani: kuyruktan oku -> tekille -> PostgreSQL'e yaz -> Redis'e canli bas.

Toplu (batch) yazar: tek tek INSERT yerine biriktirip tek transaction'da yazmak
saniyedeki mesaj kapasitesini yaklasik bir buyukluk mertebesi artiriyor.
Batch basariyla yazilmadan hicbir mesaj ack'lenmez; servis o anda cokerse
mesajlar kuyrukta kalir ve tekrar islenir (at-least-once teslimat).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import signal
import time

import aio_pika
import redis.asyncio as aioredis

from fusion import correlate
from store import (
    create_pool,
    ensure_schema,
    load_recent_tracks,
    purge_old,
    write_batch,
    write_correlations,
)
from telemetry_common import Observation, get_logger, settings
from telemetry_common.bus import connect, declare_topology

log = get_logger("processor")

STATS_KEY = "stats:processor"


class Deduplicator:
    """Ayni gozlemin tekrar islenmesini engeller.

    Redis'teki son gozlem zaman damgasi ile karsilastirir: gelen kayit
    eskiyse veya ayni ise dusurulur. Redis paylasilan durum oldugu icin
    processor birden fazla kopya halinde calistiginda da dogru sonuc verir.
    """

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    async def filter_new(self, observations: list[Observation]) -> tuple[list[Observation], int]:
        if not observations:
            return [], 0

        # Batch icinde ayni nesneden birden fazla kayit varsa en yenisi kazanir
        newest: dict[str, Observation] = {}
        intra_batch_dupes = 0
        for obs in observations:
            current = newest.get(obs.key)
            if current is None:
                newest[obs.key] = obs
            else:
                intra_batch_dupes += 1
                if obs.ts > current.ts:
                    newest[obs.key] = obs

        keys = list(newest)
        stored = await self._redis.mget([f"track:{k}" for k in keys])

        fresh: list[Observation] = []
        stale = intra_batch_dupes
        for key, raw in zip(keys, stored):
            obs = newest[key]
            if raw:
                with contextlib.suppress(Exception):
                    previous = Observation.from_dict(json.loads(raw))
                    if obs.ts <= previous.ts:
                        stale += 1
                        continue
            fresh.append(obs)
        return fresh, stale


async def publish_live(redis: aioredis.Redis, observations: list[Observation]) -> None:
    """Son durumu cache'e yazar ve canli kanaldan yayinlar."""
    pipe = redis.pipeline()
    for obs in observations:
        payload = obs.to_json()
        pipe.set(f"track:{obs.key}", payload, ex=settings.track_ttl_s)
        pipe.publish(settings.live_channel, payload)
    await pipe.execute()


async def record_stats(redis: aioredis.Redis, written: int, dropped: int, latency_ms: float) -> None:
    pipe = redis.pipeline()
    pipe.hincrby(STATS_KEY, "observations_written", written)
    pipe.hincrby(STATS_KEY, "duplicates_dropped", dropped)
    pipe.hincrby(STATS_KEY, "batches", 1)
    pipe.hset(STATS_KEY, "last_batch_ms", f"{latency_ms:.1f}")
    pipe.hset(STATS_KEY, "last_batch_at", str(int(time.time())))
    # Gecikme dagilimi icin son 500 batch suresi (p95 hesabi API tarafinda)
    pipe.lpush("stats:batch_ms", f"{latency_ms:.1f}")
    pipe.ltrim("stats:batch_ms", 0, 499)
    await pipe.execute()


async def flush(pool, redis: aioredis.Redis, dedup: Deduplicator, buffer: list, lock: asyncio.Lock) -> None:
    """Biriken mesajlari isler; basarisiz olursa mesajlari kuyruga geri verir."""
    async with lock:
        if not buffer:
            return
        started = time.perf_counter()
        messages = [m for m, _ in buffer]
        observations = [o for _, o in buffer]
        buffer.clear()

    try:
        fresh, dropped = await dedup.filter_new(observations)
        await write_batch(pool, fresh)
        await publish_live(redis, fresh)
        for message in messages:
            await message.ack()
        elapsed_ms = (time.perf_counter() - started) * 1000
        await record_stats(redis, len(fresh), dropped, elapsed_ms)
        log.info(
            "batch islendi",
            extra={"fields": {"written": len(fresh), "dropped": dropped, "duration_ms": round(elapsed_ms, 1)}},
        )
    except Exception as exc:  # noqa: BLE001
        log.error("batch yazilamadi, mesajlar kuyruga donuyor", extra={"fields": {"error": str(exc)}})
        for message in messages:
            with contextlib.suppress(Exception):
                await message.nack(requeue=True)


async def periodic_flusher(pool, redis, dedup, buffer, lock, stopping: asyncio.Event) -> None:
    """Trafik seyrekse batch dolmayabilir; yarim batch'i zaman asimiyla bosaltir."""
    interval = settings.batch_flush_ms / 1000
    while not stopping.is_set():
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stopping.wait(), timeout=interval)
        await flush(pool, redis, dedup, buffer, lock)


async def retention_worker(pool, stopping: asyncio.Event) -> None:
    """Saklama suresini asan kayitlari duzenli olarak siler.

    Disk sabit boyutlu: temizlik olmazsa tablo suresiz buyur ve disk dolunca
    sistem yazamaz hale gelir. Otomatik buyuyen bir kaynak olmadigi icin bu
    ayni zamanda beklenmedik maliyet ihtimalini de ortadan kaldirir.
    """
    while not stopping.is_set():
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stopping.wait(), timeout=settings.retention_interval_s)
        if stopping.is_set():
            return
        try:
            deleted = await purge_old(pool, settings.retention_days)
            if deleted:
                log.info(
                    "eski kayitlar silindi",
                    extra={"fields": {"deleted": deleted, "retention_days": settings.retention_days}},
                )
        except Exception as exc:  # noqa: BLE001 - temizlik hatasi pipeline'i durdurmamali
            log.error("temizlik hatasi", extra={"fields": {"error": str(exc)}})


async def correlation_worker(pool, redis: aioredis.Redis, stopping: asyncio.Event) -> None:
    """Kaynaklar arasi eslesmeleri duzenli olarak hesaplar.

    Bu, hattin tek "fuzyon" adimidir: iki bagimsiz kaynaktan gelen ve ortak
    modele normalize edilmis nesneler burada birbirine baglanir.
    """
    while not stopping.is_set():
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stopping.wait(), timeout=settings.correlation_interval_s)
        if stopping.is_set():
            return

        started = time.perf_counter()
        try:
            aircraft = await load_recent_tracks(pool, "aircraft", settings.correlation_max_age_s)
            satellites = await load_recent_tracks(pool, "satellite", settings.correlation_max_age_s)
            matches = correlate(aircraft, satellites, settings.correlation_radius_km)
            written = await write_correlations(pool, matches)

            elapsed_ms = (time.perf_counter() - started) * 1000
            await redis.hset(
                STATS_KEY,
                mapping={
                    "correlations_last_run": len(matches),
                    "correlation_ms": f"{elapsed_ms:.1f}",
                    "aircraft_tracked": len(aircraft),
                    "satellites_tracked": len(satellites),
                },
            )
            if matches:
                await redis.hincrby(STATS_KEY, "correlations_total", written)
            log.info(
                "korelasyon tamamlandi",
                extra={
                    "fields": {
                        "aircraft": len(aircraft),
                        "satellites": len(satellites),
                        "matches": len(matches),
                        "duration_ms": round(elapsed_ms, 1),
                    }
                },
            )
        except Exception as exc:  # noqa: BLE001 - korelasyon hatasi hatti durdurmamali
            log.error("korelasyon hatasi", extra={"fields": {"error": str(exc)}})


async def run() -> None:
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stopping.set)

    pool = await create_pool(settings)
    await ensure_schema(pool)
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    dedup = Deduplicator(redis)
    connection = await connect(settings)

    async with connection:
        channel = await connection.channel()
        # Prefetch = batch boyutunun iki kati: worker bosta beklemesin ama
        # bellekte de sinirsiz mesaj birikmesin (geri basinc / backpressure).
        await channel.set_qos(prefetch_count=settings.batch_size * 2)
        _, queue = await declare_topology(channel, settings)
        log.info("processor basladi", extra={"fields": {"batch_size": settings.batch_size}})

        buffer: list = []
        lock = asyncio.Lock()
        flusher = asyncio.create_task(periodic_flusher(pool, redis, dedup, buffer, lock, stopping))
        cleaner = asyncio.create_task(retention_worker(pool, stopping))
        correlator = asyncio.create_task(correlation_worker(pool, redis, stopping))

        try:
            async with queue.iterator() as messages:
                async for message in messages:
                    if stopping.is_set():
                        break
                    try:
                        obs = Observation.from_dict(json.loads(message.body))
                    except Exception:  # noqa: BLE001 - bozuk mesaj DLQ'ya gitsin
                        log.warning("cozulemeyen mesaj dead-letter kuyruguna gonderiliyor")
                        await message.reject(requeue=False)
                        continue

                    if not obs.is_valid():
                        await message.ack()
                        continue

                    buffer.append((message, obs))
                    if len(buffer) >= settings.batch_size:
                        await flush(pool, redis, dedup, buffer, lock)
        finally:
            stopping.set()
            for task in (flusher, cleaner, correlator):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await flush(pool, redis, dedup, buffer, lock)

    await redis.aclose()
    await pool.close()
    log.info("processor durdu")


if __name__ == "__main__":
    asyncio.run(run())
