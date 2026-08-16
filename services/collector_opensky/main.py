"""OpenSky ADS-B toplayicisi.

Sorumlulugu tek: dis kaynaktan veri cek, `Observation` formatina cevir, kuyruga bas.
Veritabanini hic bilmez. Kaynak duserse kuyrugu ve isleme katmanini etkilemez.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import time
from datetime import datetime, timezone
from typing import Any

import aio_pika
import httpx

from telemetry_common import Observation, get_logger, settings
from telemetry_common.bus import connect, declare_topology

log = get_logger("collector.opensky")

STATES_URL = "https://opensky-network.org/api/states/all"
TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"

# OpenSky states/all dizisindeki alan siralari (API dokumantasyonu)
IDX_ICAO24, IDX_CALLSIGN, IDX_COUNTRY = 0, 1, 2
IDX_TIME_POSITION, IDX_LON, IDX_LAT = 3, 5, 6
IDX_BARO_ALT, IDX_ON_GROUND, IDX_VELOCITY = 7, 8, 9
IDX_TRACK, IDX_VERTICAL_RATE, IDX_GEO_ALT = 10, 11, 13


class OpenSkyClient:
    """Token yenilemeyi ve hiz limitini kendi icinde yoneten istemci."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    @property
    def authenticated(self) -> bool:
        return bool(settings.opensky_client_id and settings.opensky_client_secret)

    async def _ensure_token(self) -> str | None:
        if not self.authenticated:
            return None  # anonim erisim: daha dusuk kota, yine de calisir
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token
        resp = await self._client.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": settings.opensky_client_id,
                "client_secret": settings.opensky_client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        payload = resp.json()
        self._token = payload["access_token"]
        self._token_expires_at = time.time() + float(payload.get("expires_in", 1800))
        log.info("opensky token alindi")
        return self._token

    async def fetch_states(self) -> list[list[Any]]:
        params: dict[str, float] = {}
        if settings.opensky_bbox:
            try:
                lamin, lamax, lomin, lomax = (float(p) for p in settings.opensky_bbox.split(","))
                params = {"lamin": lamin, "lamax": lamax, "lomin": lomin, "lomax": lomax}
            except ValueError:
                log.warning("OPENSKY_BBOX bicimi hatali, tum dunya cekiliyor")

        headers = {}
        token = await self._ensure_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        resp = await self._client.get(STATES_URL, params=params, headers=headers)
        if resp.status_code == 429:
            log.warning("opensky hiz limiti, bu tur atlaniyor")
            return []
        resp.raise_for_status()
        return resp.json().get("states") or []


def to_observation(state: list[Any]) -> Observation | None:
    """Ham OpenSky dizisini ortak modele cevirir."""
    try:
        icao24 = (state[IDX_ICAO24] or "").strip()
        lat, lon = state[IDX_LAT], state[IDX_LON]
        if lat is None or lon is None:
            return None

        ts_epoch = state[IDX_TIME_POSITION]
        ts = datetime.fromtimestamp(ts_epoch, tz=timezone.utc) if ts_epoch else datetime.now(timezone.utc)
        callsign = (state[IDX_CALLSIGN] or "").strip() or None
        altitude = state[IDX_GEO_ALT] if state[IDX_GEO_ALT] is not None else state[IDX_BARO_ALT]

        obs = Observation(
            source="opensky",
            source_id=icao24,
            ts=ts,
            lat=float(lat),
            lon=float(lon),
            altitude_m=float(altitude) if altitude is not None else None,
            speed_mps=float(state[IDX_VELOCITY]) if state[IDX_VELOCITY] is not None else None,
            heading_deg=float(state[IDX_TRACK]) if state[IDX_TRACK] is not None else None,
            vertical_rate_mps=float(state[IDX_VERTICAL_RATE]) if state[IDX_VERTICAL_RATE] is not None else None,
            label=callsign,
            country=state[IDX_COUNTRY] or None,
            on_ground=bool(state[IDX_ON_GROUND]),
        )
        return obs if obs.is_valid() else None
    except (IndexError, TypeError, ValueError):
        return None


async def publish_batch(exchange: aio_pika.abc.AbstractExchange, observations: list[Observation]) -> None:
    for obs in observations:
        await exchange.publish(
            aio_pika.Message(
                body=obs.to_json().encode(),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ),
            routing_key="raw.opensky",
        )


async def run() -> None:
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stopping.set)

    connection = await connect(settings)
    async with connection:
        channel = await connection.channel(publisher_confirms=True)
        exchange, _ = await declare_topology(channel, settings)
        log.info("collector basladi", extra={"fields": {"interval_s": settings.opensky_poll_interval_s}})

        async with httpx.AsyncClient(timeout=30.0) as http:
            client = OpenSkyClient(http)
            while not stopping.is_set():
                started = time.perf_counter()
                try:
                    states = await client.fetch_states()
                    observations = [obs for obs in (to_observation(s) for s in states) if obs]
                    await publish_batch(exchange, observations)
                    log.info(
                        "tur tamamlandi",
                        extra={
                            "fields": {
                                "received": len(states),
                                "published": len(observations),
                                "dropped": len(states) - len(observations),
                                "duration_ms": round((time.perf_counter() - started) * 1000),
                            }
                        },
                    )
                except Exception as exc:  # noqa: BLE001 - kaynak hatasi servisi dusurmemeli
                    log.error("cekme hatasi", extra={"fields": {"error": str(exc)}})

                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stopping.wait(), timeout=settings.opensky_poll_interval_s)

    log.info("collector durdu")


if __name__ == "__main__":
    asyncio.run(run())
