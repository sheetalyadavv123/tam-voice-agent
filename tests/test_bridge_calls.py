"""The bridge's call handling with LiveKit replaced by a fake leg."""
import asyncio
import json
import logging
import time

import pytest
from fastapi.testclient import TestClient
from livekit import api

import bridge.app as app_mod
import bridge.livekit_leg as leg_mod
from bridge.logs import DropLiveKitInfo


class AgentHangsUp:
    """The agent joins, then ends the call a moment later."""

    def __init__(self, call_number, customer_id, on_audio, on_utterance_end, on_clear,
                 on_agent_left, timeline=None):
        self.on_agent_left = on_agent_left

    async def start(self):
        async def later():
            await asyncio.sleep(0.05)
            await self.on_agent_left()
        self.task = asyncio.create_task(later())

    async def caller_audio(self, ulaw):
        pass

    async def close(self):
        pass


class LegBreaks(AgentHangsUp):
    """A real failure while the call is up."""

    async def start(self):
        pass

    async def caller_audio(self, ulaw):
        raise ValueError("boom")


def open_call(monkeypatch, leg_class, call_number):
    monkeypatch.setattr(app_mod, "LiveKitLeg", leg_class)
    bot_id = f"bot-{call_number}"
    app_mod.pending[bot_id] = {"payload": {"call_number": call_number}, "customer_id": "C002",
                               "expires": time.monotonic() + 30, "t0": time.monotonic()}
    return TestClient(app_mod.app).websocket_connect(f"/ws/{bot_id}")


def bridge_lines(caplog, call_number):
    return [(r.levelno, r.getMessage()) for r in caplog.records
            if r.name == "bridge" and call_number in r.getMessage()]


def test_our_own_close_logs_ended_not_failed(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    with open_call(monkeypatch, AgentHangsUp, "c-ok") as ws:
        ws.send_text(json.dumps({"event": "start", "start": {}}))
        msg = json.loads(ws.receive_text())
        assert msg["event"] == "stop"
        assert msg["stop"]["reason"] == "callended"
    lines = bridge_lines(caplog, "c-ok")
    assert (logging.INFO, "call c-ok ended") in lines
    assert not [m for lvl, m in lines if lvl >= logging.ERROR], lines


def test_a_real_error_still_logs_failed(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    with open_call(monkeypatch, LegBreaks, "c-bad") as ws:
        ws.send_text(json.dumps({"event": "media", "media": {"payload": "AAAA"}}))
    lines = bridge_lines(caplog, "c-bad")
    assert (logging.ERROR, "call c-bad failed") in lines
    assert not [m for lvl, m in lines if m == "call c-bad ended"]


# Room cleanup

class FakeRoomService:
    def __init__(self, error):
        self.error = error

    async def delete_room(self, req):
        if self.error:
            raise self.error


class FakeAPI:
    error = None

    def __init__(self, *args, **kwargs):
        self.room = FakeRoomService(FakeAPI.error)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def close_leg(monkeypatch, error):
    monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")
    monkeypatch.setenv("LIVEKIT_API_KEY", "key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "secret")
    FakeAPI.error = error
    monkeypatch.setattr(leg_mod.api, "LiveKitAPI", FakeAPI)

    async def nothing(*a):
        pass

    leg = leg_mod.LiveKitLeg("c-room", "C002", nothing, nothing, nothing, nothing)
    asyncio.run(leg.close())


@pytest.mark.parametrize("error", [
    api.TwirpError("unknown", "", status=404),           # 404 without a Twirp JSON body
    api.TwirpError("not_found", "room not found", status=404),
])
def test_room_already_deleted_is_normal(monkeypatch, caplog, error):
    caplog.set_level(logging.INFO)
    close_leg(monkeypatch, error)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert "room sampark-c-room already deleted" in caplog.text


def test_other_delete_errors_still_warn(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    close_leg(monkeypatch, api.TwirpError("permission_denied", "no", status=403))
    assert [r for r in caplog.records if r.levelno == logging.WARNING]


# Log noise

def record(name, level, path, msg):
    return logging.LogRecord(name, level, path, 1, msg, (), None)


def test_livekit_stream_noise_is_dropped_but_not_our_lines_or_livekit_warnings():
    f = DropLiveKitInfo()
    room_py = "/venv/lib/site-packages/livekit/rtc/room.py"
    assert not f.filter(record("root", logging.INFO, room_py,
                               "ignoring byte stream with topic 'lk.agent.session'"))
    assert f.filter(record("root", logging.WARNING, room_py, "something wrong"))
    assert f.filter(record("bridge", logging.INFO, "/app/bridge/app.py", "call c1 ended"))
