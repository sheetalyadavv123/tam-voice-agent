"""A live call through the SAMPARK bridge, with your mic and speakers.
USE HEADPHONES, or the agent will hear itself.

Usage: python scripts/phone_call.py --token test123 --customer C002
"""
import argparse
import asyncio
import base64
import json
import sys
import threading
import time
from pathlib import Path

import httpx
import numpy as np
import websockets

sys.path.insert(0, str(Path(__file__).parent.parent))
from bridge.audio import pcm16_to_ulaw, ulaw_to_pcm16  # noqa: E402

DEVICE_RATE = 48000
FACTOR = DEVICE_RATE // 8000  # 6
BLOCK = 160 * FACTOR          # 20 ms at 48 kHz


class Speaker:
    def __init__(self):
        import sounddevice as sd  # here, not at the top: tests import this file without audio devices
        self.buf = bytearray()
        self.lock = threading.Lock()
        self.stream = sd.OutputStream(samplerate=DEVICE_RATE, channels=1, dtype="int16",
                                      blocksize=BLOCK, callback=self._play)

    def _play(self, outdata, frames, time_info, status):
        need = frames * 2
        with self.lock:
            data = bytes(self.buf[:need])
            del self.buf[:need]
        outdata[:] = np.frombuffer(data.ljust(need, b"\0"), dtype=np.int16).reshape(-1, 1)

    def add(self, pcm8: np.ndarray):
        with self.lock:
            self.buf += np.repeat(pcm8, FACTOR).astype(np.int16).tobytes()

    def clear(self):
        with self.lock:
            self.buf.clear()

    def empty(self):
        with self.lock:
            return not self.buf


async def hang_up(ws, call_number):
    """Ctrl+C: tell the bridge we hung up, as a real dialer does, then close."""
    try:
        await asyncio.wait_for(ws.send(json.dumps({
            "event": "stop", "call_number": call_number, "stop": {"reason": "hangup"}})), 1)
        await asyncio.wait_for(ws.close(), 2)
    except (websockets.ConnectionClosed, asyncio.TimeoutError, OSError):
        pass


async def run_call(ws, call_number, mic_q, speaker):
    """Send mic audio and play the agent until the bridge sends "stop" or the socket
    closes, then stop sending and return. Cancelled (Ctrl+C): hang up first."""
    ended = asyncio.Event()

    async def listen():
        try:
            async for raw in ws:
                msg = json.loads(raw)
                event = msg.get("event")
                if event == "media":
                    speaker.add(ulaw_to_pcm16(base64.b64decode(msg["media"]["payload"])))
                elif event == "mark":
                    while not speaker.empty():
                        await asyncio.sleep(0.02)
                    await ws.send(raw)
                elif event == "clear":
                    speaker.clear()
                    print("[clear]")
                elif event == "xfer":
                    print("[xfer]", msg.get("xfer"))
                elif event == "stop":
                    print("[stop]", msg.get("stop"))
                    return
        except websockets.ConnectionClosed:
            pass
        finally:
            ended.set()

    listener = asyncio.create_task(listen())
    seq = 0
    try:
        while not ended.is_set():
            try:
                pcm8 = await asyncio.wait_for(mic_q.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            if ended.is_set():
                break
            seq += 1
            try:
                await ws.send(json.dumps({"event": "media", "call_number": call_number,
                                          "media": {"chunk_seq_no": seq, "timestamp": seq * 20,
                                                    "payload": base64.b64encode(pcm16_to_ulaw(pcm8)).decode()}}))
            except websockets.ConnectionClosed:
                break
    except asyncio.CancelledError:
        await hang_up(ws, call_number)
        raise
    finally:
        try:
            if not listener.done():
                listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
        finally:
            print("Call ended.")


async def main(args):
    import sounddevice as sd
    call_number = f"live-{int(time.time())}"
    payload = {"to_number": "9999999999", "call_number": call_number, "campaign_id": "TEST",
               "customer_data": {"CUSTOMER_ID": args.customer}}
    headers = {"x-sampark-token": args.token} if args.token else {}
    r = httpx.post(f"{args.bridge}/sampark", json=payload, headers=headers, timeout=10)
    r.raise_for_status()
    url = r.json()["url"]

    loop = asyncio.get_running_loop()
    mic_q: asyncio.Queue = asyncio.Queue()

    def mic(indata, frames, time_info, status):
        pcm48 = indata[:, 0].astype(np.int32)
        pcm8 = pcm48.reshape(-1, FACTOR).mean(axis=1).astype(np.int16)
        loop.call_soon_threadsafe(mic_q.put_nowait, pcm8)

    speaker = Speaker()
    speaker.stream.start()
    try:
        async with websockets.connect(url) as ws:
            await ws.send(json.dumps({
                "event": "start", "call_number": call_number, "campaign_id": "TEST",
                "bot_id": url.rsplit("/", 1)[-1],
                "start": {"to": payload["to_number"], "customer_data": payload["customer_data"],
                          "media_format": {"encoding": "mulaw", "sample_rate": "8000", "bit_rate": "8"}}}))
            with sd.InputStream(samplerate=DEVICE_RATE, channels=1, dtype="int16",
                                blocksize=BLOCK, callback=mic):
                print("Call connected. Talk now. Ctrl+C to hang up.")
                await run_call(ws, call_number, mic_q, speaker)
    finally:
        speaker.stream.stop()


def cli(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--bridge", default="http://localhost:8080")
    p.add_argument("--token", default="")
    p.add_argument("--customer", default="C002")
    try:
        asyncio.run(main(p.parse_args(argv)))
    except KeyboardInterrupt:
        pass  # Ctrl+C: run_call already hung up and said "Call ended."


if __name__ == "__main__":
    cli()