"""The agent greets only once the caller is in the room; a caller who never comes is
recorded as such, whichever way the job ends."""
import ast
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "agent"))
import main  # noqa: E402

import bridge.app as bridge_app  # noqa: E402
from tools.collection_tools import OUTCOMES  # noqa: E402
from tools.rules import now_ist  # noqa: E402
from tools.store import NO_CALLER, MemoryStore  # noqa: E402


class FakeParticipant:
    identity = "caller-c1"


class FakeCtx:
    """A job context: the caller joins after joins_after seconds (None: never)."""

    def __init__(self, joins_after=None, fake_job=False):
        self.joins_after = joins_after
        self.fake_job = fake_job
        self.waited = False
        self.shutdown_reason = None
        self.callbacks = []
        self.job = SimpleNamespace(metadata="")
        self.room = SimpleNamespace(name="sampark-c1", on=lambda event: (lambda fn: fn))

    def is_fake_job(self):
        return self.fake_job

    async def wait_for_participant(self):
        self.waited = True
        if self.joins_after is None:
            await asyncio.Event().wait()
        await asyncio.sleep(self.joins_after)
        return FakeParticipant()

    def add_shutdown_callback(self, fn):
        self.callbacks.append(fn)

    def shutdown(self, reason):
        self.shutdown_reason = reason

    async def run_shutdown(self):
        """What the job does on shutdown (ctx.shutdown, or the room deleted)."""
        for fn in self.callbacks:
            await fn()


# wait_for_caller on its own

def wait(ctx, timeout=1.0):
    return asyncio.run(main.wait_for_caller(ctx, timeout=timeout))


def test_caller_already_in_the_room():
    assert wait(FakeCtx(joins_after=0)) is True


def test_waits_for_a_caller_who_comes_later():
    async def go():
        ctx = FakeCtx(joins_after=0.2)
        task = asyncio.create_task(main.wait_for_caller(ctx, timeout=1.0))
        await asyncio.sleep(0.1)
        assert not task.done()  # still waiting: no greeting yet
        return await task
    assert asyncio.run(go()) is True


def test_nobody_comes():
    assert wait(FakeCtx(joins_after=None), timeout=0.05) is False


def test_console_mode_does_not_wait():
    ctx = FakeCtx(joins_after=None, fake_job=True)
    assert wait(ctx, timeout=0.05) is True
    assert not ctx.waited


def test_the_wait_ends_before_the_bridge_drops_the_room():
    # The bridge deletes an unused room URL_TTL s after /sampark; the agent's wait starts
    # seconds later (dispatch, data load), so it must be clearly shorter
    assert main.CALLER_WAIT_SECONDS <= bridge_app.URL_TTL - 5


# The whole entrypoint, with LiveKit and the AI replaced by fakes

class FakeSession:
    def __init__(self, **kwargs):
        self.said = []
        self.history = SimpleNamespace(items=[])
        self.user_state = "listening"

    def on(self, event):
        return lambda fn: fn

    async def start(self, **kwargs):
        pass

    async def say(self, text):
        self.said.append(text)


@pytest.fixture
def agent(monkeypatch, tmp_path):
    """Runs main.entrypoint for customer C002; returns what it saved."""
    saved = {"calls": [], "store": None, "session": None}

    async def schema_ok():
        return True

    async def memory_store(customer_id):
        saved["store"] = MemoryStore.from_files(clock=now_ist)
        return saved["store"], False

    async def save_call(session_id, room_name, channel, started_at, ended_at, turns, outcome=None,
                        metadata=None, conn=None):
        saved["calls"].append({"room": room_name, "turns": turns, "outcome": outcome,
                               "metadata": metadata})

    def session(**kwargs):
        saved["session"] = FakeSession(**kwargs)
        return saved["session"]

    monkeypatch.setattr(main.config, "TEST_MODE", False)
    monkeypatch.setattr(main, "check_schema", schema_ok)
    monkeypatch.setattr(main, "build_store", memory_store)
    monkeypatch.setattr(main.rules, "can_call_now", lambda *a: {"allowed": True, "reason": None})
    monkeypatch.setattr(main, "AgentSession", session)
    monkeypatch.setattr(main, "build_llm", lambda: None)
    monkeypatch.setattr(main, "build_tts", lambda: None)
    monkeypatch.setattr(main.deepgram, "STT", lambda **k: None)
    monkeypatch.setattr(main.silero.VAD, "load", lambda **k: None)
    monkeypatch.setattr(main.noise_cancellation, "BVC", lambda **k: None)
    monkeypatch.setattr(main.db, "save_call", save_call)
    monkeypatch.setattr(main, "CALLER_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(main, "TRANSCRIPT_DIR", tmp_path)
    return saved


def attempt_outcome(saved):
    [attempt] = saved["store"].attempts_for("C002")
    return attempt["outcome"]


def test_no_caller_is_saved_with_its_own_outcome(agent):
    ctx = FakeCtx(joins_after=None)

    async def go():
        await main.entrypoint(ctx)       # times out, asks the job to shut down
        assert ctx.shutdown_reason == "caller never joined"
        await ctx.run_shutdown()         # the job's shutdown runs the save hook
    asyncio.run(go())

    assert agent["session"].said == []   # no greeting into an empty room
    [call] = agent["calls"]              # the call record is saved, with no turns
    assert call["turns"] == []
    assert call["outcome"] == NO_CALLER
    assert call["metadata"]["note"] == "caller never joined the room"
    assert attempt_outcome(agent) == NO_CALLER


def test_room_deleted_while_waiting_is_recorded_the_same_way(agent, monkeypatch):
    """The bridge deleted the room before the wait ended: the job shuts down with the
    entrypoint still waiting, and the shutdown hook records the outcome."""
    monkeypatch.setattr(main, "CALLER_WAIT_SECONDS", 30)
    ctx = FakeCtx(joins_after=None)

    async def go():
        task = asyncio.create_task(main.entrypoint(ctx))
        await asyncio.sleep(0.1)
        assert not task.done()           # still waiting for the caller
        await ctx.run_shutdown()         # room gone: the job shuts down
        task.cancel()
    asyncio.run(go())

    assert agent["session"].said == []
    [call] = agent["calls"]
    assert call["outcome"] == NO_CALLER
    assert attempt_outcome(agent) == NO_CALLER


def test_a_caller_who_joins_is_greeted_and_is_not_a_no_caller(agent):
    ctx = FakeCtx(joins_after=0)

    async def go():
        await main.entrypoint(ctx)
        await ctx.run_shutdown()
    asyncio.run(go())

    assert len(agent["session"].said) == 1   # the greeting
    assert ctx.shutdown_reason is None
    assert attempt_outcome(agent) != NO_CALLER


def test_no_caller_is_not_an_outcome_the_ai_can_pick():
    assert NO_CALLER not in OUTCOMES


def test_no_caller_does_not_lift_a_wrong_number_hold():
    store = MemoryStore.from_files(clock=now_ist)
    phone = store.get_customer("C002").get("phone")
    t = now_ist()
    store.add_attempt("C002", t.replace(hour=10), "s1", phone=phone)
    store.set_attempt_outcome("s1", "wrong_number")
    store.add_attempt("C002", t.replace(hour=11), "s2", phone=phone)
    store.set_attempt_outcome("s2", NO_CALLER)
    assert store.number_check_hold("C002")["outcome"] == "wrong_number"


# Order inside entrypoint

def _entrypoint_calls():
    """The calls in entrypoint, in source order, as text."""
    tree = ast.parse(Path(main.__file__).read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "entrypoint")
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
    calls.sort(key=lambda n: (n.lineno, n.col_offset))
    return [ast.unparse(n.func) for n in calls]


def test_entrypoint_order():
    calls = _entrypoint_calls()
    hook, start, wait_at, say = (calls.index("ctx.add_shutdown_callback"),
                                 calls.index("session.start"), calls.index("wait_for_caller"),
                                 calls.index("session.say"))
    assert hook < wait_at        # the save hook is in place before the wait
    assert start < wait_at < say
    # no caller: the job ends (and saves the call) instead of greeting an empty room
    assert wait_at < calls.index("ctx.shutdown") < say
