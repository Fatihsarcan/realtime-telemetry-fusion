"""Kaynaklar arasi korelasyon: fuzyonun asil yapildigi yer.

Iki bagimsiz kaynaktan gelen nesneler ayni ortak modele normalize edildikten
sonra, uzamsal olarak eslestirilebilir hale gelir. Burada uretilen soru su:

    "Su an hangi uydunun yer izdusumu, hangi ucagin uzerinden geciyor?"

Savunma ve gozetleme sistemlerinde bunun karsiligi kapsama analizidir: belirli
bir hedefi o anda hangi platformun gorebilecegi. Kaynaklarin biri ADS-B telsiz
yayini, digeri yorunge elemanlarindan hesaplanmis konum; ortak modele
cevrilmeselerdi bu sorgu yazilamazdi.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from telemetry_common.models import haversine_m


@dataclass(frozen=True)
class Correlation:
    a: dict[str, Any]
    b: dict[str, Any]
    distance_km: float


def _lat_window_deg(radius_km: float) -> float:
    """Yaricapin enlem cinsinden karsiligi (1 derece enlem ~ 111 km)."""
    return radius_km / 111.0


def correlate(
    primary: list[dict[str, Any]],
    secondary: list[dict[str, Any]],
    radius_km: float,
) -> list[Correlation]:
    """Iki kumedeki nesneleri verilen yaricap icinde eslestirir.

    Once ucuz bir enlem bandi elemesi yapilir, ancak o eleme sonrasi kalan
    adaylar icin pahali haversine hesabi calisir. Kaba kuvvet karsilastirma
    bu boyutlarda yeterli; kume buyudugunde bir arada (grid) indeksleme veya
    PostGIS'e tasima dogal sonraki adimdir.
    """
    if not primary or not secondary:
        return []

    lat_window = _lat_window_deg(radius_km)
    # Enleme gore siralamak, bandin disina cikildiginda erken cikmayi saglar
    secondary_sorted = sorted(secondary, key=lambda r: r["lat"])
    lats = [r["lat"] for r in secondary_sorted]

    results: list[Correlation] = []
    for a in primary:
        lo = _bisect_left(lats, a["lat"] - lat_window)
        hi = _bisect_right(lats, a["lat"] + lat_window)
        for b in secondary_sorted[lo:hi]:
            distance_km = haversine_m(a["lat"], a["lon"], b["lat"], b["lon"]) / 1000.0
            if distance_km <= radius_km:
                results.append(Correlation(a=a, b=b, distance_km=round(distance_km, 2)))
    return results


def _bisect_left(values: list[float], target: float) -> int:
    lo, hi = 0, len(values)
    while lo < hi:
        mid = (lo + hi) // 2
        if values[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _bisect_right(values: list[float], target: float) -> int:
    lo, hi = 0, len(values)
    while lo < hi:
        mid = (lo + hi) // 2
        if values[mid] <= target:
            lo = mid + 1
        else:
            hi = mid
    return lo


def elevation_angle_deg(ground_distance_km: float, altitude_m: float | None) -> float | None:
    """Uydunun ufuktan yukseklik acisi (kaba tahmin).

    Kapsama analizinde mesafe tek basina yeterli degildir: alcak acili gecisler
    pratikte gorus saglamaz. Duz zemin yaklasikligi kullanilir, kisa mesafelerde
    yeterince dogrudur.
    """
    if not altitude_m or ground_distance_km < 0:
        return None
    if ground_distance_km == 0:
        return 90.0
    return round(math.degrees(math.atan2(altitude_m / 1000.0, ground_distance_km)), 1)
