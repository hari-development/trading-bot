import asyncio
import websockets
import sys

async def test_ws():
    uri = sys.argv[1]
    try:
        async with websockets.connect(uri) as ws:
            print("Connected!")
            res = await ws.recv()
            print("Received:", res[:100], "...")
    except Exception as e:
        print("Failed:", e)

asyncio.run(test_ws())
