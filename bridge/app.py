"""SAMPARK bridge: the dialer's HTTP + WebSocket bot API, connected to the
LiveKit agent. One LiveKit room per call."""
import asyncio
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketDisconnected

from bridge import logs
from bridge.audio import b64_to_ulaw
from bridge.livekit_leg import LiveKitLeg
from bridge.outgoing import Outgoing
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
DATA = Path(__file__).parent / "data"

app = FastAPI()
pending = {}


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
    bot_id = secrets.token_urlsafe(16)
    pending[bot_id] = {"payload": body.model_dump(), "customer_id": str(customer_id),
                       "expires": time.monotonic() + URL_TTL, "t0": time.monotonic()}
    log.info("call %s for customer %s, bot_id %s", body.call_number, customer_id, bot_id)
    return {"url": f"{PUBLIC_WS_BASE}/ws/{bot_id}"}


@app.websocket("/ws/{bot_id}")
async def media_ws(ws: WebSocket, bot_id: str):
    entry = pending.pop(bot_id, None)
    await ws.accept()
    if entry is None or entry["expires"] < time.monotonic():
        log.warning("rejected websocket: unknown, used or expired bot_id")
        await ws.close(code=4401)
        return

    call_number = entry["payload"]["call_number"]
    timeline = Timeline(call_number, entry.get("t0"))
    timeline.mark("websocket connected")

    async def send(msg):
        await ws.send_text(json.dumps(msg))

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
        except (WebSocketDisconnect, WebSocketDisconnected, RuntimeError):
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
    except (WebSocketDisconnect, WebSocketDisconnected) as e:
        if ended_by_us:
            log.info("call %s ended", call_number)
        elif isinstance(e, WebSocketDisconnect):
            log.info("dialer hung up %s", call_number)
        else:
            log.exception("call %s failed", call_number)
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