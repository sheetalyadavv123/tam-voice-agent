"""Pretends to be the SAMPARK dialer: POST /sampark, connect, start, stream a WAV
as mu-law, echo marks, save what the bot sent.

Usage: python scripts/fake_dialer.py --wav audio_test/test_8k.wav --token test123
"""
import argparse
import asyncio
import base64
import json
import sys
import time
import wave
from pathlib import Path

import httpx
import numpy as np
import websockets

sys.path.insert(0, str(Path(__file__).parent.parent))
from bridge.audio import chunks, pcm16_to_ulaw, ulaw_to_pcm16  # noqa: E402


def read_wav_8k(path):
    with wave.open(path, "rb") as w:
        if w.getframerate() != 8000 or w.getnchannels() != 1 or w.getsampwidth() != 2:
            sys.exit("WAV must be 8 kHz, mono, 16-bit. Convert with:\n"
                     "ffmpeg -i in.wav -ar 8000 -ac 1 -sample_fmt s16 out.wav")
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


async def main(args):
    call_number = f"test-{int(time.time())}"
    payload = {"to_number": "9999999999", "call_number": call_number, "campaign_id": "TEST",
               "customer_data": {"CUSTOMER_ID": args.customer}}
    headers = {"x-sampark-token": args.token} if args.token else {}
    r = httpx.post(f"{args.bridge}/sampark", json=payload, headers=headers, timeout=5)
    r.raise_for_status()
    url = r.json()["url"]
    print("websocket:", url)

    received = bytearray()
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({
            "event": "start", "call_number": call_number, "campaign_id": "TEST",
            "bot_id": url.rsplit("/", 1)[-1],
            "start": {"to": payload["to_number"], "customer_data": payload["customer_data"],
                      "media_format": {"encoding": "mulaw", "sample_rate": "8000", "bit_rate": "8"}}}))

        async def listen():
            playing = bytearray()
            async for raw in ws:
                msg = json.loads(raw)
                event = msg.get("event")
                if event == "media":
                    audio = base64.b64decode(msg["media"]["payload"])
                    received.extend(audio)
                    playing.extend(audio)
                elif event == "mark":
                    await asyncio.sleep(len(playing) / 8000)  # "play" it first
                    playing.clear()
                    print("played", msg["mark"]["name"], "-> sending mark back")
                    await ws.send(raw)
                elif event == "clear":
                    playing.clear()
                    print("clear")
                elif event == "xfer":
                    print("xfer:", msg.get("xfer"))
                elif event == "stop":
                    print("stop:", msg.get("stop"))
                    return

        listener = asyncio.create_task(listen())
        ulaw = pcm16_to_ulaw(read_wav_8k(args.wav))
        for seq, chunk in enumerate(chunks(ulaw), 1):
            await ws.send(json.dumps({"event": "media", "call_number": call_number,
                                      "media": {"chunk_seq_no": seq, "timestamp": seq * 20,
                                                "payload": base64.b64encode(chunk).decode()}}))
            await asyncio.sleep(0.02)  # real time
        await asyncio.sleep(args.wait)
        listener.cancel()

    out = Path(args.out)
    out.parent.mkdir(exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(ulaw_to_pcm16(bytes(received)).tobytes())
    print(f"saved what the bot sent: {out} ({len(received) / 8000:.1f} s)")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--bridge", default="http://localhost:8080")
    p.add_argument("--token", default="")
    p.add_argument("--wav", required=True)
    p.add_argument("--customer", default="C002")
    p.add_argument("--wait", type=float, default=6)
    p.add_argument("--out", default="audio_test/bot_reply.wav")
    asyncio.run(main(p.parse_args()))