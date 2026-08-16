"""WebSocket canli akis dogrulamasi.

Kullanim (api konteyneri icinden calisir, ek bagimlilik gerektirmez):
    docker compose exec -T api python - < scripts/ws_smoke.py
"""

import asyncio
import json
import sys

import websockets

URL = "ws://localhost:8000/ws/live"
WANTED = 3
TIMEOUT = 45


async def main() -> int:
    async with websockets.connect(URL) as ws:
        received = 0
        while received < WANTED:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=TIMEOUT)
            except asyncio.TimeoutError:
                print("HATA: zaman asimi, canli mesaj gelmedi")
                return 1
            d = json.loads(raw)
            print(
                "CANLI: {:8} lat={:8.3f} lon={:8.3f} alt={} hiz={}".format(
                    d.get("label") or d["source_id"], d["lat"], d["lon"],
                    d.get("altitude_m"), d.get("speed_mps"),
                )
            )
            received += 1
    print(f"OK: {received} canli mesaj alindi")
    return 0


sys.exit(asyncio.run(main()))
