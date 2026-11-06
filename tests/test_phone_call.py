"""The fake dialer stops sending and exits cleanly when the bridge ends the call."""
import asyncio
import importlib.util
import json
from pathlib import Path

import numpy as np
import websockets

spec = importlib.util.spec_from_file_location(
    "phone_call", Path(__file__).parent.parent / "scripts" / "phone_call.py")
phone_call = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phone_call)


class QuietSpeaker:
    def add(self, pcm):
        pass

    def clear(self):
        pass

    def empty(self):
        return True


def run(bridge_side):
    """A bridge that runs bridge_side(ws, got) and a dialer whose mic never stops."""
    got = []

    async def handler(ws):
        await bridge_side(ws, got)

    async def go():
        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            mic_q = asyncio.Queue()

            async def mic():
                while True:
                    mic_q.put_nowait(np.zeros(160, np.int16))
                    await asyncio.sleep(0.005)

            feeding = asyncio.create_task(mic())
            try:
                async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                    await asyncio.wait_for(
                        phone_call.run_call(ws, "c1", mic_q, QuietSpeaker()), timeout=5)
            finally:
                feeding.cancel()

    asyncio.run(go())
    return got


def test_stops_on_stop_and_says_call_ended(capsys):
    async def bridge_side(ws, got):
        got.append(json.loads(await ws.recv())["event"])   # some mic audio arrives
        await ws.send(json.dumps({"event": "mark", "mark": {"name": "utt_1"}}))
        while True:  # the mark comes back
            msg = json.loads(await ws.recv())
            if msg["event"] == "mark":
                got.append("mark")
                break
        await ws.send(json.dumps({"event": "stop", "stop": {"reason": "callended"}}))
        await asyncio.sleep(0.1)  # mic audio keeps coming in while the dialer notices
        await ws.close()

    got = run(bridge_side)
    assert got == ["media", "mark"]
    out = capsys.readouterr().out
    assert "[stop]" in out
    assert "Call ended." in out


def test_stops_when_the_socket_closes_without_stop(capsys):
    async def bridge_side(ws, got):
        await ws.recv()
        await ws.close()

    run(bridge_side)
    assert "Call ended." in capsys.readouterr().out
