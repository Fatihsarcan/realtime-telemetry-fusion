"""Tum servislerin ortak yapilandirmasi. Degerler ortam degiskenlerinden okunur."""

import os
from dataclasses import dataclass, field


def _env(key: str, default: str) -> str:
    return os.getenv(key, default)


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # --- Altyapi baglantilari ---
    amqp_url: str = field(default_factory=lambda: _env("AMQP_URL", "amqp://guest:guest@rabbitmq:5672/"))
    redis_url: str = field(default_factory=lambda: _env("REDIS_URL", "redis://redis:6379/0"))
    postgres_dsn: str = field(
        default_factory=lambda: _env("POSTGRES_DSN", "postgresql://telemetry:telemetry@postgres:5432/telemetry")
    )

    # --- Mesajlasma topolojisi ---
    exchange: str = field(default_factory=lambda: _env("AMQP_EXCHANGE", "telemetry"))
    raw_queue: str = field(default_factory=lambda: _env("AMQP_RAW_QUEUE", "raw.observations"))
    raw_routing_key: str = field(default_factory=lambda: _env("AMQP_RAW_ROUTING_KEY", "raw.#"))

    # Redis pub/sub: processor yayinlar, API WebSocket'e dagitir
    live_channel: str = field(default_factory=lambda: _env("LIVE_CHANNEL", "tracks.live"))
    # Redis'te tutulan son konum kaydinin yasam suresi (saniye)
    track_ttl_s: int = field(default_factory=lambda: _env_int("TRACK_TTL_S", 900))

    # --- Collector ayarlari ---
    opensky_poll_interval_s: float = field(default_factory=lambda: _env_float("OPENSKY_POLL_INTERVAL_S", 15.0))
    opensky_client_id: str = field(default_factory=lambda: _env("OPENSKY_CLIENT_ID", ""))
    opensky_client_secret: str = field(default_factory=lambda: _env("OPENSKY_CLIENT_SECRET", ""))
    # Bos birakilirsa tum dunya cekilir. "lamin,lamax,lomin,lomax" formati.
    opensky_bbox: str = field(default_factory=lambda: _env("OPENSKY_BBOX", "35.5,42.5,25.5,45.0"))

    # --- Processor ayarlari ---
    batch_size: int = field(default_factory=lambda: _env_int("PROCESSOR_BATCH_SIZE", 500))
    batch_flush_ms: int = field(default_factory=lambda: _env_int("PROCESSOR_BATCH_FLUSH_MS", 1000))

    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))


settings = Settings()
