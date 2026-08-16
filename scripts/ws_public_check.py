"""Canli akisin internet uzerinden (tunel/HTTPS arkasindan) calistigini dogrular.

WebSocket'ler ters vekil sunucularda sik sik bozulur; bu kontrol yayinin
disaridan gercekten aktigini kanitlar.

Kullanim:
    docker compose exec -T -e PUBLIC_WS_URL=wss://ornek/ws/live api python - < scripts/ws_public_check.py
"""

import asyncio
import json
import os
import sys

import websockets

URL = os.getenv("PUBLIC_WS_URL", "ws://localhost:8000/ws/live")
WANTED = 2
TIMEOUT = 90


async def main() -> int:
    print(f"baglaniliyor: {URL}")
    async with websockets.connect(URL, open_timeout=30) as ws:
        for i in range(1, WANTED + 1):
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=TIMEOUT)
            except asyncio.TimeoutError:
                print("HATA: zaman asimi, canli mesaj gelmedi")
                return 1
            d = json.loads(raw)
            print(f"  mesaj {i}: {d.get('label') or d['source_id']} @ {d['lat']:.3f},{d['lon']:.3f}")
    print("OK: canli akis internet uzerinden calisiyor")
    return 0


sys.exit(asyncio.run(main()))
