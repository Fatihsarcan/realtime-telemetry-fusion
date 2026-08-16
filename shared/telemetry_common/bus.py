"""RabbitMQ yardimcilari.

Topoloji: `telemetry` topic exchange -> `raw.observations` kuyrugu.
Kuyrukta islenemeyen mesajlar `telemetry.dlx` uzerinden `raw.observations.dead`
kuyruguna dusuyor; boylece bozuk mesaj pipeline'i kilitlemiyor.
"""

from __future__ import annotations

import asyncio

import aio_pika

from .config import Settings
from .log import get_logger

log = get_logger("bus")

DLX_SUFFIX = ".dlx"
DEAD_SUFFIX = ".dead"


async def connect(cfg: Settings, attempts: int = 30, delay: float = 2.0) -> aio_pika.abc.AbstractRobustConnection:
    """RabbitMQ hazir olana kadar yeniden dener (compose'da sira garantisi yok)."""
    last: Exception | None = None
    for i in range(1, attempts + 1):
        try:
            return await aio_pika.connect_robust(cfg.amqp_url)
        except Exception as exc:  # noqa: BLE001 - baglanti hatalarinin tumu beklenen
            last = exc
            log.warning("rabbitmq baglantisi bekleniyor", extra={"fields": {"attempt": i}})
            await asyncio.sleep(delay)
    raise RuntimeError(f"RabbitMQ'ya baglanilamadi: {last}")


async def declare_topology(
    channel: aio_pika.abc.AbstractChannel, cfg: Settings
) -> tuple[aio_pika.abc.AbstractExchange, aio_pika.abc.AbstractQueue]:
    dlx = await channel.declare_exchange(cfg.exchange + DLX_SUFFIX, aio_pika.ExchangeType.FANOUT, durable=True)
    dead = await channel.declare_queue(cfg.raw_queue + DEAD_SUFFIX, durable=True)
    await dead.bind(dlx)

    exchange = await channel.declare_exchange(cfg.exchange, aio_pika.ExchangeType.TOPIC, durable=True)
    queue = await channel.declare_queue(
        cfg.raw_queue,
        durable=True,
        arguments={"x-dead-letter-exchange": cfg.exchange + DLX_SUFFIX},
    )
    await queue.bind(exchange, routing_key=cfg.raw_routing_key)
    return exchange, queue
