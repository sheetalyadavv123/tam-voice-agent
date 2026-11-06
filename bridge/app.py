"""SAMPARK bridge: the dialer's HTTP + WebSocket bot API, connected to the
LiveKit agent. One LiveKit room per call."""
import asyncio
import json
import logging
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketDisconnected
from uvicorn.protocols.utils import ClientDisconnected
from websockets.exceptions import ConnectionClosed

from bridge import logs
from bridge.audio import b64_to_ulaw
from bridge.livekit_leg import LiveKitLeg
from bridge.outgoing import Outgoing
from bridge.rooms import room_name, rooms
from bridge.timeline import Timeline

load_dotenv()
logs.setup()
log = logging.getLogger("bridge")

TOKEN = os.getenv("SAMPARK_TOKEN", "")
ALLOWED_IPS = {ip.strip() for ip in os.getenv("SAMPARK_ALLOWED_IPS", "").split(",") if ip.strip()}
POSTCALL_KEY = os.getenv("POSTCALL_API_KEY", "")
PUBLIC_WS_BASE = os.getenv("PUBLIC_WS_BASE", "ws://localhost:8080")
URL_TTL = 30
LAST_WORDS_WAIT = 10  # seconds to let the agent's last sentence play before "stop"
# /sampark waits this long for the room; after that the URL goes out anyway, and a room
# that then fails closes the WebSocket with 1011 (see docs/DECISIONS.md, 2026-11-06)
ROOM_WAIT = 1.0
DATA = Path(__file__).parent / "data"

# The dialer is gone, whichever layer noticed first
DISCONNECTS = (WebSocketDisconnect, WebSocketDisconnected, ClientDisconnected, ConnectionClosed,
               ConnectionResetError, BrokenPipeError)


@asynccontextmanager
async def lifespan(app):
    yield
    await rooms.aclose()


app = FastAPI(lifespan=lifespan)
pending = {}
background = set()  # keeps the expiry tasks alive


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
    customer_id = body.customer_data.get("CUSTOMER_ID") or body.customer_data.get("customer_id")
    if not customer_id:
        raise HTTPException(400, "customer_data.CUSTOMER_ID is required")
    call_number = body.call_number
    timeline = Timeline(call_number)
    name = room_name(call_number)
    meta = {"customer_id": str(customer_id), "call_number": call_number,
            "kind": "reminder", "source": "sampark"}
    # The room, and with it the agent's dispatch, starts now, while the dialer connects
    room = asyncio.create_task(rooms.create(name, meta))
    room.add_done_callback(lambda t: t.cancelled() or t.exception() or timeline.mark("room created"))
    done, _ = await asyncio.wait({room}, timeout=ROOM_WAIT)
    if done and room.exception():
        log.error("call %s: could not create room %s: %r", call_number, name, room.exception())
        raise HTTPException(502, "could not create the call room")
    bot_id = secrets.token_urlsafe(16)
    pending[bot_id] = {"payload": body.model_dump(), "customer_id": str(customer_id),
                       "expires": time.monotonic() + URL_TTL, "timeline": timeline,
                       "room": room, "room_name": name}
    expiry = asyncio.create_task(expire(bot_id))
    background.add(expiry)
    expiry.add_done_callback(background.discard)
    log.info("call %s for customer %s, bot_id %s", call_number, customer_id, bot_id)
    return {"url": f"{PUBLIC_WS_BASE}/ws/{bot_id}"}


async def drop_room(entry):
    """The dialer never used this URL: remove the room so the agent doesn't wait in it."""
    try:
        await entry["room"]
    except Exception:
        return  # never created
    await rooms.delete(entry["room_name"])


async def expire(bot_id):
    await asyncio.sleep(URL_TTL)
    entry = pending.pop(bot_id, None)
    if entry is not None:
        log.warning("call %s: the dialer never connected, removing room %s",
                    entry["payload"]["call_number"], entry["room_name"])
        await drop_room(entry)


@app.websocket("/ws/{bot_id}")
async def media_ws(ws: WebSocket, bot_id: str):
    entry = pending.pop(bot_id, None)
    await ws.accept()
    if entry is None or entry["expires"] < time.monotonic():
        log.warning("rejected websocket: unknown, used or expired bot_id")
        await ws.close(code=4401)
        if entry is not None:
            await drop_room(entry)
        return

    call_number = entry["payload"]["call_number"]
    timeline = entry["timeline"]
    timeline.mark("websocket connected")
    try:
        await entry["room"]  # usually ready already; if not, wait for it here
    except Exception as e:
        log.error("call %s: could not create room %s: %r", call_number, entry["room_name"], e)
        await ws.close(code=1011)  # the dialer must treat 1011 as a failed call
        return

    async def send(msg):
        try:
            await ws.send_text(json.dumps(msg))
        except DISCONNECTS:
            pass  # the dialer is gone; the receive loop sees it and ends the call

    out = Outgoing(send, call_number)
    agent_gone = asyncio.Event()
    ended_by_us = False  # we sent "stop" and closed the socket: a disconnect after that is normal

    async def agent_left():
        agent_gone.set()

    leg = LiveKitLeg(call_number, entry["customer_id"], on_audio=out.audio,
                     on_utterance_end=out.end, on_clear=out.clear, on_agent_left=agent_left,
                     timeline=timeline)

    async def stop_when_agent_leaves():
        nonlocal ended_by_us
        await agent_gone.wait()
        await out.end()
        for _ in range(LAST_WORDS_WAIT * 10):
            if out.idle():
                break
            await asyncio.sleep(0.1)
        log.info("agent ended call %s: stop", call_number)
        ended_by_us = True
        try:
            await send({"event": "stop", "call_number": call_number, "stop": {"reason": "callended"}})
            await ws.close()
        except (*DISCONNECTS, RuntimeError):
            pass  # the dialer hung up at the same moment: nothing left to tell it

    closer = asyncio.create_task(stop_when_agent_leaves())
    try:
        await leg.start()
        while True:
            msg = json.loads(await ws.receive_text())
            event = msg.get("event")
            if event == "start":
                log.info("start %s, format %s", call_number,
                         msg.get("start", {}).get("media_format", {}))
            elif event == "media":
                await leg.caller_audio(b64_to_ulaw(msg["media"]["payload"]))
            elif event == "mark":
                log.info("mark back: %s", msg.get("mark", {}).get("name"))
                await out.mark_back()
            elif event == "stop":
                log.info("stop from dialer: %s", msg.get("stop"))
                break
    except DISCONNECTS:
        if ended_by_us:
            log.info("call %s ended", call_number)
        else:
            log.info("dialer hung up %s", call_number)
    except Exception:
        log.exception("call %s failed", call_number)
    finally:
        closer.cancel()
        await leg.close()


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