"""SAMPARK bridge: the dialer's HTTP + WebSocket bot API.

Part 1: the bot echoes the caller's audio back in 2-second pieces, so the
protocol (start, media, mark, stop) can be tested end to end.
"""
import asyncio
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from bridge.audio import SAMPLE_RATE, b64_to_ulaw, chunks, ulaw_to_b64

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bridge")

TOKEN = os.getenv("SAMPARK_TOKEN", "")
ALLOWED_IPS = {ip.strip() for ip in os.getenv("SAMPARK_ALLOWED_IPS", "").split(",") if ip.strip()}
POSTCALL_KEY = os.getenv("POSTCALL_API_KEY", "")
PUBLIC_WS_BASE = os.getenv("PUBLIC_WS_BASE", "ws://localhost:8080")
URL_TTL = 30  # seconds a WebSocket URL stays valid
ECHO_BYTES = SAMPLE_RATE * 2  # echo in 2-second pieces
DATA = Path(__file__).parent / "data"

app = FastAPI()
pending = {}  # bot_id -> {"payload": ..., "expires": ...}


class CallRequest(BaseModel):
    to_number: str
    call_number: str
    campaign_id: str = ""
    customer_data: dict = Field(default_factory=dict)


def check_caller(request: Request):
    ip = request.client.host if request.client else ""
    if ALLOWED_IPS and ip not in ALLOWED_IPS:
        raise HTTPException(403, "ip not allowed")
    if TOKEN:
        given = request.headers.get("x-sampark-token") or request.query_params.get("token", "")
        if not secrets.compare_digest(given, TOKEN):
            raise HTTPException(401, "bad token")


@app.post("/sampark")
async def sampark(body: CallRequest, request: Request):
    check_caller(request)
    bot_id = secrets.token_urlsafe(16)
    pending[bot_id] = {"payload": body.model_dump(), "expires": time.monotonic() + URL_TTL}
    customer = body.customer_data.get("CUSTOMER_ID", "?")
    log.info("call %s for customer %s, bot_id %s", body.call_number, customer, bot_id)
    return {"url": f"{PUBLIC_WS_BASE}/ws/{bot_id}"}


class CallSession:
    def __init__(self, ws: WebSocket, call_number: str):
        self.ws = ws
        self.call_number = call_number
        self.mark_back = asyncio.Event()
        self.mark_back.set()  # nothing playing yet
        self.seq = 0
        self.marks = 0

    async def send(self, msg: dict):
        await self.ws.send_text(json.dumps(msg))

    async def speak(self, ulaw: bytes):
        """One piece of speech: all its 20 ms chunks, then a mark.
        SAMPARK rule: never send new audio before the last mark comes back."""
        await self.mark_back.wait()
        self.mark_back.clear()
        for chunk in chunks(ulaw):
            self.seq += 1
            await self.send({"event": "media", "call_number": self.call_number,
                             "media": {"chunk_seq_no": self.seq, "timestamp": self.seq * 20,
                                       "payload": ulaw_to_b64(chunk)}})
        self.marks += 1
        await self.send({"event": "mark", "call_number": self.call_number,
                         "mark": {"name": f"utt_{self.marks}"}})

    async def clear(self):
        await self.send({"event": "clear"})
        self.mark_back.set()

    async def stop(self, reason: str = "callended"):
        await self.send({"event": "stop", "call_number": self.call_number,
                         "stop": {"reason": reason}})


@app.websocket("/ws/{bot_id}")
async def media_ws(ws: WebSocket, bot_id: str):
    entry = pending.pop(bot_id, None)  # one-time URL
    await ws.accept()
    if entry is None or entry["expires"] < time.monotonic():
        log.warning("rejected websocket: unknown, used or expired bot_id")
        await ws.close(code=4401)
        return

    session = CallSession(ws, entry["payload"]["call_number"])
    outbox: asyncio.Queue = asyncio.Queue()

    async def speaker():
        while True:
            await session.speak(await outbox.get())

    speaker_task = asyncio.create_task(speaker())
    heard = bytearray()
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            event = msg.get("event")
            if event == "start":
                fmt = msg.get("start", {}).get("media_format", {})
                log.info("start %s, format %s", session.call_number, fmt)
            elif event == "media":
                heard += b64_to_ulaw(msg["media"]["payload"])
                if len(heard) >= ECHO_BYTES:
                    await outbox.put(bytes(heard))
                    heard.clear()
            elif event == "mark":
                log.info("mark back: %s", msg.get("mark", {}).get("name"))
                session.mark_back.set()
            elif event == "stop":
                log.info("stop from dialer: %s", msg.get("stop"))
                break
            else:
                log.info("unknown event: %s", event)
    except WebSocketDisconnect:
        log.info("dialer disconnected %s", session.call_number)
    finally:
        speaker_task.cancel()


@app.post("/calldata/postcall")
async def postcall(request: Request):
    if POSTCALL_KEY and not secrets.compare_digest(request.headers.get("apikey", ""), POSTCALL_KEY):
        raise HTTPException(401, "bad apikey")
    data = await request.json()
    call_number = re.sub(r"[^A-Za-z0-9_-]", "_", str(data.get("call_number", "unknown")))
    DATA.mkdir(exist_ok=True)
    (DATA / f"postcall_{call_number}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    log.info("post-call data saved for %s", call_number)
    return {"status": "ok"}