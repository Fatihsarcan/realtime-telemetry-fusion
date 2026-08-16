"""IP basina WebSocket baglanti sinirinin gercekten uygulandigini dogrular.

MAX_WS_PER_IP=5 iken 6. baglanti reddedilmeli (kapanis kodu 1013).

Kullanim:
    docker compose exec -T api python - < scripts/ws_limit_test.py
"""

import asyncio
import sys

import websockets

URL = "ws://localhost:8000/ws/live"
ATTEMPTS = 7


async def main() -> int:
    acik = []
    reddedilen = 0

    for i in range(1, ATTEMPTS + 1):
        try:
            ws = await websockets.connect(URL, open_timeout=5)
            acik.append(ws)
            print(f"  baglanti {i}: kabul edildi")
        except Exception as exc:  # noqa: BLE001
            reddedilen += 1
            print(f"  baglanti {i}: REDDEDILDI ({type(exc).__name__})")

    for ws in acik:
        await ws.close()

    print(f"\nkabul={len(acik)} reddedilen={reddedilen}")
    if reddedilen > 0 and len(acik) <= 5:
        print("OK: IP basina sinir uygulaniyor")
        return 0
    print("HATA: sinir uygulanmadi, tum baglantilar kabul edildi")
    return 1


sys.exit(asyncio.run(main()))
