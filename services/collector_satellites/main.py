"""Uydu telemetrisi toplayicisi (Celestrak TLE + SGP4).

ADS-B toplayicisindan yapisal olarak farkli bir kaynak, ve bu proje icin
onemli olan da bu:

  - OpenSky bize dogrudan konum verir; burada konum YOK. Elimizde yalnizca
    yorunge elemanlari (TLE) var, konumu SGP4 ile kendimiz hesapliyoruz.
  - TLE saatlerce gecerli kalir, bu yuzden dis servise 6 saatte bir gidilir.
    Konumlar ise saniyeler icinde lokalde uretilir; kota sinirlamasi yok.

Iki kaynak da ayni `Observation` modeline normalize edilir; hattin geri kalani
verinin nereden geldigini bilmez.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import time
from datetime import datetime, timezone

import aio_pika
import httpx
from skyfield.api import EarthSatellite, load, wgs84

from telemetry_common import Observation, get_logger, settings
from telemetry_common.bus import connect, declare_topology

log = get_logger("collector.satellites")

TLE_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT=tle"


class SatelliteCatalog:
    """TLE kataloglarini tutar ve gerektiginde yeniler."""

    def __init__(self) -> None:
        self._timescale = load.timescale()
        self._satellites: list[EarthSatellite] = []
        self._loaded_at: float = 0.0

    @property
    def size(self) -> int:
        return len(self._satellites)

    def is_stale(self, max_age_s: float) -> bool:
        return not self._satellites or (time.time() - self._loaded_at) > max_age_s

    async def refresh(self, client: httpx.AsyncClient, group: str) -> None:
        """Celestrak'ten TLE katalogunu ceker. Anahtar veya kota gerektirmez."""
        resp = await client.get(TLE_URL.format(group=group), timeout=60)
        resp.raise_for_status()

        lines = [line.rstrip() for line in resp.text.splitlines() if line.strip()]
        satellites: list[EarthSatellite] = []
        # TLE bicimi: ad satiri + iki elemanlar satiri
        for i in range(0, len(lines) - 2, 3):
            name, line1, line2 = lines[i], lines[i + 1], lines[i + 2]
            if not line1.startswith("1 ") or not line2.startswith("2 "):
                continue  # bozuk kayit, atla
            with contextlib.suppress(Exception):
                satellites.append(EarthSatellite(line1, line2, name.strip(), self._timescale))

        if not satellites:
            raise ValueError("TLE katalogu bos veya cozulemedi")

        self._satellites = satellites
        self._loaded_at = time.time()
        log.info("TLE katalogu yenilendi", extra={"fields": {"group": group, "count": len(satellites)}})

    def positions(self) -> list[Observation]:
        """Her uydunun su anki yer izdusumunu hesaplar."""
        now = self._timescale.now()
        ts = datetime.now(timezone.utc)
        observations: list[Observation] = []

        for sat in self._satellites:
            try:
                geocentric = sat.at(now)
                subpoint = wgs84.subpoint(geocentric)
                # Yer merkezli hiz vektorunun buyuklugu (km/s -> m/s)
                speed_mps = float(geocentric.velocity.km_per_s.dot(geocentric.velocity.km_per_s) ** 0.5 * 1000)
            except Exception:  # noqa: BLE001 - tek uydunun cozulmemesi turu bozmamali
                continue

            obs = Observation(
                source="celestrak",
                source_id=str(sat.model.satnum),
                object_type="satellite",
                ts=ts,
                lat=float(subpoint.latitude.degrees),
                lon=float(subpoint.longitude.degrees),
                altitude_m=float(subpoint.elevation.m),
                speed_mps=speed_mps,
                label=sat.name,
                on_ground=False,
            )
            if obs.is_valid():
                observations.append(obs)

        return observations


async def publish(exchange: aio_pika.abc.AbstractExchange, observations: list[Observation]) -> None:
    for obs in observations:
        await exchange.publish(
            aio_pika.Message(
                body=obs.to_json().encode(),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ),
            routing_key="raw.celestrak",
        )


async def run() -> None:
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stopping.set)

    catalog = SatelliteCatalog()
    connection = await connect(settings)

    async with connection:
        channel = await connection.channel(publisher_confirms=True)
        exchange, _ = await declare_topology(channel, settings)
        log.info(
            "uydu toplayicisi basladi",
            extra={"fields": {"group": settings.satellite_group, "interval_s": settings.satellite_interval_s}},
        )

        async with httpx.AsyncClient(timeout=60.0) as http:
            while not stopping.is_set():
                started = time.perf_counter()
                try:
                    if catalog.is_stale(settings.tle_refresh_s):
                        await catalog.refresh(http, settings.satellite_group)

                    observations = catalog.positions()
                    await publish(exchange, observations)
                    log.info(
                        "tur tamamlandi",
                        extra={
                            "fields": {
                                "catalog_size": catalog.size,
                                "published": len(observations),
                                "duration_ms": round((time.perf_counter() - started) * 1000),
                            }
                        },
                    )
                except Exception as exc:  # noqa: BLE001 - kaynak hatasi servisi dusurmemeli
                    log.error("uydu turu basarisiz", extra={"fields": {"error": str(exc)}})

                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stopping.wait(), timeout=settings.satellite_interval_s)

    log.info("uydu toplayicisi durdu")


if __name__ == "__main__":
    asyncio.run(run())
