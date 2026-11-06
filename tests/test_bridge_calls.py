"""The bridge's call handling with LiveKit replaced by fakes."""
import asyncio
import json
import logging
import time

import pytest
from fastapi.testclient import TestClient
from livekit import api
from starlette.websockets import WebSocketDisconnect, WebSocketDisconnected
from uvicorn.protocols.utils import ClientDisconnected

import bridge.app as app_mod
import bridge.rooms as rooms_mod
from bridge.logs import DropLiveKitInfo
from bridge.timeline import Timeline


class FakeRooms:
    def __init__(self, delay=0.0, error=None):
        self.delay, self.error = delay, error
        self.created, self.deleted = [], []

    async def create(self, name, metadata):
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        self.created.append(name)

    async def delete(self, name):
        self.deleted.append(name)

    async def aclose(self):
        pass


class AgentHangsUp:
    """The agent joins, then ends the call a moment later."""
    started_with_rooms = None

    def __init__(self, call_number, customer_id, on_audio, on_utterance_end, on_clear,
                 on_agent_left, timeline=None):
        self.on_agent_left = on_agent_left

    async def start(self):
        AgentHangsUp.started_with_rooms = list(app_mod.rooms.created)

        async def later():
            await asyncio.sleep(0.05)
            await self.on_agent_left()
        self.task = asyncio.create_task(later())

    async def caller_audio(self, ulaw):
        pass

    async def close(self):
        pass


class Quiet(AgentHangsUp):
    """The agent stays on the line."""

    async def start(self):
        AgentHangsUp.started_with_rooms = list(app_mod.rooms.created)


def leg_raising(error):
    class Leg(Quiet):
        async def caller_audio(self, ulaw):
            raise error
    return Leg


@pytest.fixture
def bridge(monkeypatch):
    monkeypatch.setattr(app_mod, "TOKEN", "")
    monkeypatch.setattr(app_mod, "ALLOWED_IPS", set())
    fake = FakeRooms()
    monkeypatch.setattr(app_mod, "rooms", fake)
    return fake


def open_call(monkeypatch, leg_class, call_number):
    """A call whose room is already there, as after a normal /sampark."""
    monkeypatch.setattr(app_mod, "LiveKitLeg", leg_class)
    bot_id = f"bot-{call_number}"
    app_mod.pending[bot_id] = {"payload": {"call_number": call_number}, "customer_id": "C002",
                               "expires": time.monotonic() + 30, "timeline": Timeline(call_number),
                               "room": DoneRoom(), "room_name": f"sampark-{call_number}"}
    return TestClient(app_mod.app).websocket_connect(f"/ws/{bot_id}")


class DoneRoom:
    """Awaitable like a finished room-creation task."""

    def __await__(self):
        return iter(())


def bridge_lines(caplog, call_number):
    return [(r.levelno, r.getMessage()) for r in caplog.records
            if r.name == "bridge" and call_number in r.getMessage()]


def errors(lines):
    return [m for lvl, m in lines if lvl >= logging.ERROR]


def post_call(client, call_number):
    return client.post("/sampark", json={"to_number": "9999999999", "call_number": call_number,
                                         "customer_data": {"CUSTOMER_ID": "C002"}})


# Hang-ups

def test_our_own_close_logs_ended_not_failed(bridge, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    with open_call(monkeypatch, AgentHangsUp, "c-ok") as ws:
        ws.send_text(json.dumps({"event": "start", "start": {}}))
        msg = json.loads(ws.receive_text())
        assert msg["event"] == "stop"
        assert msg["stop"]["reason"] == "callended"
    lines = bridge_lines(caplog, "c-ok")
    assert (logging.INFO, "call c-ok ended") in lines
    assert not errors(lines), lines


def test_dialer_closing_the_socket_is_a_hang_up(bridge, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    with open_call(monkeypatch, Quiet, "c-bye") as ws:
        ws.send_text(json.dumps({"event": "start", "start": {}}))
    lines = bridge_lines(caplog, "c-bye")
    assert (logging.INFO, "dialer hung up c-bye") in lines
    assert not errors(lines), lines


@pytest.mark.parametrize("error", [
    # What Ctrl+C in phone_call.py produced: a send to the gone dialer failed, Starlette
    # marked the socket closed, and the next receive raised WebSocketDisconnected
    WebSocketDisconnected('WebSocket is not connected. Need to call "accept" first.'),
    ClientDisconnected(),
    ConnectionResetError(),
])
def test_any_disconnect_error_is_a_hang_up(bridge, monkeypatch, caplog, error):
    caplog.set_level(logging.INFO)
    with open_call(monkeypatch, leg_raising(error), "c-gone") as ws:
        ws.send_text(json.dumps({"event": "media", "media": {"payload": "AAAA"}}))
    lines = bridge_lines(caplog, "c-gone")
    assert (logging.INFO, "dialer hung up c-gone") in lines
    assert not errors(lines), lines


def test_a_real_error_still_logs_failed(bridge, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    with open_call(monkeypatch, leg_raising(ValueError("boom")), "c-bad") as ws:
        ws.send_text(json.dumps({"event": "media", "media": {"payload": "AAAA"}}))
    lines = bridge_lines(caplog, "c-bad")
    assert (logging.ERROR, "call c-bad failed") in lines
    assert not [m for lvl, m in lines if "hung up" in m or m.endswith("ended")]


# The room is made at /sampark

def test_room_is_created_before_the_url_goes_out(bridge, monkeypatch):
    monkeypatch.setattr(app_mod, "LiveKitLeg", AgentHangsUp)
    with TestClient(app_mod.app) as client:
        r = post_call(client, "c-early")
        assert r.status_code == 200
        assert bridge.created == ["sampark-c-early"]
        path = "/ws/" + r.json()["url"].rsplit("/", 1)[-1]
        with client.websocket_connect(path) as ws:
            assert json.loads(ws.receive_text())["event"] == "stop"
    assert AgentHangsUp.started_with_rooms == ["sampark-c-early"]


def test_room_failure_answers_sampark_with_an_error(bridge, monkeypatch):
    bridge.error = api.TwirpError("unauthenticated", "bad key", status=401)
    before = dict(app_mod.pending)
    with TestClient(app_mod.app) as client:
        r = post_call(client, "c-fail")
    assert r.status_code == 502
    assert "url" not in r.json()
    assert app_mod.pending == before


def test_a_slow_room_is_waited_for_by_the_websocket(bridge, monkeypatch):
    monkeypatch.setattr(app_mod, "ROOM_WAIT", 0.01)
    monkeypatch.setattr(app_mod, "LiveKitLeg", AgentHangsUp)
    bridge.delay = 0.3
    with TestClient(app_mod.app) as client:
        r = post_call(client, "c-slow")
        assert r.status_code == 200
        assert bridge.created == []  # the URL went out first
        path = "/ws/" + r.json()["url"].rsplit("/", 1)[-1]
        with client.websocket_connect(path) as ws:
            assert json.loads(ws.receive_text())["event"] == "stop"
    assert AgentHangsUp.started_with_rooms == ["sampark-c-slow"]  # joined only once it existed


def test_a_slow_room_that_fails_closes_the_websocket(bridge, monkeypatch, caplog):
    monkeypatch.setattr(app_mod, "ROOM_WAIT", 0.01)
    monkeypatch.setattr(app_mod, "LiveKitLeg", AgentHangsUp)
    bridge.delay = 0.1
    bridge.error = api.TwirpError("unavailable", "down", status=503)
    AgentHangsUp.started_with_rooms = None
    with TestClient(app_mod.app) as client:
        r = post_call(client, "c-late-fail")
        path = "/ws/" + r.json()["url"].rsplit("/", 1)[-1]
        with client.websocket_connect(path) as ws:
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_text()
    assert closed.value.code == 1011  # the dialer's signal for a failed call
    assert AgentHangsUp.started_with_rooms is None
    assert any("could not create room sampark-c-late-fail" in r.getMessage() for r in caplog.records)


def test_an_unused_url_removes_its_room(bridge, monkeypatch):
    monkeypatch.setattr(app_mod, "URL_TTL", 0.1)
    with TestClient(app_mod.app) as client:
        assert post_call(client, "c-noshow").status_code == 200
        time.sleep(0.4)
    assert bridge.deleted == ["sampark-c-noshow"]


# One LiveKit client for the process; room cleanup

class FakeRoomService:
    def __init__(self, error):
        self.error = error

    async def create_room(self, req):
        pass

    async def delete_room(self, req):
        if self.error:
            raise self.error


class FakeAPI:
    error = None
    made = 0

    def __init__(self, *args, **kwargs):
        FakeAPI.made += 1
        self.room = FakeRoomService(FakeAPI.error)

    async def aclose(self):
        pass


@pytest.fixture
def lk(monkeypatch):
    monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")
    monkeypatch.setenv("LIVEKIT_API_KEY", "key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "secret")
    FakeAPI.error, FakeAPI.made = None, 0
    monkeypatch.setattr(rooms_mod.api, "LiveKitAPI", FakeAPI)
    return FakeAPI


def test_one_client_is_reused_across_calls(lk):
    async def calls():
        r = rooms_mod.Rooms()
        for n in range(3):
            await r.create(f"sampark-c{n}", {})
            await r.delete(f"sampark-c{n}")
        await r.aclose()
    asyncio.run(calls())
    assert lk.made == 1


def delete_with(error):
    FakeAPI.error = error
    asyncio.run(rooms_mod.Rooms().delete("sampark-c-room"))


@pytest.mark.parametrize("error", [
    api.TwirpError("unknown", "", status=404),           # 404 without a Twirp JSON body
    api.TwirpError("not_found", "room not found", status=404),
])
def test_room_already_deleted_is_normal(lk, caplog, error):
    caplog.set_level(logging.INFO)
    delete_with(error)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert "room sampark-c-room already deleted" in caplog.text


def test_other_delete_errors_still_warn(lk, caplog):
    caplog.set_level(logging.INFO)
    delete_with(api.TwirpError("permission_denied", "no", status=403))
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
