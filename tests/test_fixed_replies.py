"""The agent answers some questions itself, with fixed lines, and the AI doesn't get the turn."""
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "agent"))
import main  # noqa: E402
from livekit.agents import StopResponse, llm  # noqa: E402

from tools import caller_words, policy  # noqa: E402
from tools.collection_tools import Call  # noqa: E402
from tools.rules import IST  # noqa: E402
from tools.store import MemoryStore  # noqa: E402

NOW = datetime(2026, 10, 28, 11, 0, tzinfo=IST)
FAR = datetime(2100, 1, 1, tzinfo=IST)


class FakeHandle:
    async def wait_for_playout(self):
        pass


class FakeSession:
    def __init__(self):
        self.said, self.closed, self.history = [], False, []

    def _conversation_item_added(self, item):
        self.history.append(item)

    def say(self, text):
        self.said.append(text)
        return FakeHandle()

    def shutdown(self):
        self.closed = True


@pytest.fixture(autouse=True)
def default_mode(monkeypatch):
    monkeypatch.setattr(policy, "VERIFICATION_MODE", "name_only")


def make_agent(customer="C002"):
    store = MemoryStore.from_files(clock=lambda: NOW)
    call = Call(store, customer, session_id="s1", clock=lambda: NOW)
    agent = main.Collector(call, "x")
    session = FakeSession()
    agent._session_for_test = session
    return agent, call, store, session


@pytest.fixture(autouse=True)
def fake_session(monkeypatch):
    monkeypatch.setattr(main.Collector, "session",
                        property(lambda self: self._session_for_test))


def hear(agent, text):
    """Runs what the agent does when the caller finishes a message. True if the AI is skipped."""
    async def go():
        try:
            msg = llm.ChatMessage(role="user", content=[text])
            await agent.on_user_turn_completed(None, msg)
        except StopResponse:
            if getattr(agent, "_end_task", None):
                await agent._end_task
            return True
        return False
    return asyncio.run(go())


# ---- step 3: legal questions go to a human ----

def test_legal_question_hands_over_and_ends_the_call(caplog):
    agent, call, store, session = make_agent()
    call.report_person("customer")
    with caplog.at_level(logging.WARNING, logger="tam-voice-agent"):
        assert hear(agent, "case hoga kya?") is True
    assert "LEGAL HANDOFF" in caplog.text
    assert session.said == [caller_words.LEGAL_LINE["hi"]]
    assert session.closed is True
    assert len(store.callbacks_due(FAR)) == 1
    assert call.outcome == "human_callback" and call.handoff_requested


def test_legal_question_keeps_one_callback_even_after_an_earlier_handover():
    agent, call, store, session = make_agent()
    call.report_person("customer")
    call.transfer_to_human("hardship")
    hear(agent, "court jaana padega kya?")
    assert len(store.callbacks_due(FAR)) == 1


def test_legal_words_before_identity_do_not_hand_over():
    agent, call, store, session = make_agent()
    assert hear(agent, "case hai kya koi?") is False
    assert call.handoff_requested is False and store.callbacks_due(FAR) == []


def test_ordinary_answer_goes_to_the_ai():
    agent, call, store, session = make_agent()
    call.report_person("customer")
    assert hear(agent, "शुक्रवार को दे दूँगी") is False
    assert session.said == [] and call.caller_lines == ["शुक्रवार को दे दूँगी"]


# ---- step 4a: third parties get the fixed line ----

def test_sister_gets_the_fixed_line_on_her_first_question():
    agent, call, store, session = make_agent()
    assert hear(agent, "main unki behen hoon, kya kaam hai?") is True
    assert call.person_status == "third_party"
    assert session.said == [caller_words.line(
        caller_words.THIRD_PARTY_LINE, "hi", name=call.first_name)]
    assert "personal" in session.said[0]


def test_known_third_party_asking_again_gets_the_same_line():
    agent, call, store, session = make_agent()
    call.report_person("third_party")
    assert hear(agent, "loan ka hai kya?") is True
    assert hear(agent, "क्या काम है?") is True
    assert len(session.said) == 2 and session.said[0] == session.said[1]


def test_unknown_person_asking_why_goes_to_the_ai():
    # could be the customer themselves; the AI asks who it is speaking with
    agent, call, store, session = make_agent()
    assert hear(agent, "kya kaam hai?") is False
    assert call.person_status is None


def test_stopped_turn_still_lands_in_the_transcript():
    agent, call, store, session = make_agent()
    call.report_person("customer")
    hear(agent, "case hoga kya?")
    assert [m.text_content for m in session.history] == ["case hoga kya?"]
    assert agent.chat_ctx.items[-1].text_content == "case hoga kya?"


def test_call_note_has_no_year_on_spoken_dates():
    store = MemoryStore.from_files(clock=lambda: NOW)
    call = Call(store, "C002", session_id="s1", clock=lambda: NOW)
    note = main.call_note(call, store.get_customer("C002"), "reminder", NOW)
    assert "EMI was due on: 10 September\n" in note
    assert "Today: Wednesday 28 October 2026" in note   # the year stays for the tools
    assert "Next 7 days: Thursday 29 October," in note
