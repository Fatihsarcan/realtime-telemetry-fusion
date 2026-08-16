"""Ortak veri modeli.

Sistemin fuzyon (data fusion) katmani burada baslar: her kaynak kendi formatinda
veri gonderir (OpenSky ADS-B, AIS, deprem servisi...), hepsi tek bir `Observation`
seklinde normalize edilir. Downstream servisler kaynak formatini hic bilmez.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Observation:
    """Bir kaynagin, bir nesne icin, bir andaki gozlemi."""

    source: str  # "opensky", "celestrak", ...
    source_id: str  # icao24 / NORAD katalog numarasi / istasyon kodu
    ts: datetime  # gozlem zamani (UTC)
    lat: float
    lon: float
    # Nesnenin turu kaynagindan bagimsizdir: ayni tur birden fazla kaynaktan
    # gelebilir. Korelasyon ve gorsellestirme buna gore yapilir.
    object_type: str = "unknown"  # "aircraft" | "satellite"
    altitude_m: float | None = None
    speed_mps: float | None = None
    heading_deg: float | None = None
    vertical_rate_mps: float | None = None
    label: str | None = None  # callsign / gemi adi
    country: str | None = None
    on_ground: bool = False
    ingested_at: datetime = field(default_factory=utcnow)
    raw: dict[str, Any] = field(default_factory=dict)

    def is_valid(self) -> bool:
        """Bozuk/eksik kayitlari pipeline'in basinda eler."""
        if not self.source_id:
            return False
        for value in (self.lat, self.lon):
            if value is None or math.isnan(value) or math.isinf(value):
                return False
        if not (-90.0 <= self.lat <= 90.0) or not (-180.0 <= self.lon <= 180.0):
            return False
        # (0,0) noktasi ADS-B'de neredeyse her zaman hatali kayittir
        if self.lat == 0.0 and self.lon == 0.0:
            return False
        return True

    @property
    def is_airborne_object(self) -> bool:
        """Korelasyona girecek nesneler: yerdeki ucaklar hesaba katilmaz."""
        return not self.on_ground

    @property
    def key(self) -> str:
        """Kaynaklar arasi cakismayan tekil anahtar."""
        return f"{self.source}:{self.source_id}"

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["ts"] = self.ts.isoformat()
        data["ingested_at"] = self.ingested_at.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Observation":
        payload = dict(data)
        payload["ts"] = _parse_ts(payload.get("ts"))
        payload["ingested_at"] = _parse_ts(payload.get("ingested_at"))
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in payload.items() if k in known})


def _parse_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return utcnow()


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Iki koordinat arasi mesafe (metre). Dedup ve korelasyonda kullanilir."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
