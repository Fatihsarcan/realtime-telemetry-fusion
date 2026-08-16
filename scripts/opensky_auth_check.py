"""OpenSky kimlik dogrulamasini teshis eder.

Gizli degerleri asla yazdirmaz; yalnizca uzunluk, bicim ve sunucunun
dondurdugu hata kodunu gosterir.

Kullanim:
    docker compose exec -T collector-opensky python - < scripts/opensky_auth_check.py
"""

import os
import sys

import httpx

TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"

cid = os.getenv("OPENSKY_CLIENT_ID", "")
sec = os.getenv("OPENSKY_CLIENT_SECRET", "")

print(f"client_id   : {len(cid)} karakter, bicim ok={cid.isprintable() and cid == cid.strip()}")
print(f"client_secret: {len(sec)} karakter, bicim ok={sec.isprintable() and sec == sec.strip()}")
if cid:
    print(f"client_id ilk 4 / son 2 : {cid[:4]}...{cid[-2:]}")

if not cid or not sec:
    print("HATA: degerlerden biri bos")
    sys.exit(1)

resp = httpx.post(
    TOKEN_URL,
    data={"grant_type": "client_credentials", "client_id": cid, "client_secret": sec},
    headers={"Content-Type": "application/x-www-form-urlencoded"},
    timeout=30,
)

print(f"\nHTTP {resp.status_code}")
if resp.status_code == 200:
    print("OK: token alindi")
    sys.exit(0)

# Hata govdesi kimlik bilgisi icermez, guvenle yazdirilabilir
print(f"sunucu cevabi: {resp.text[:300]}")
sys.exit(1)
