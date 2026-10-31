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

    def reset_away_timer(self):
        self.away_resets = getattr(self, "away_resets", 0) + 1


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
    assert "RULE legal_handoff" in caplog.text
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


# ---- batch 2: lines the code speaks itself ----

def run_tool(agent, coro):
    """Runs one agent tool. Returns its result, or "stopped" when the code spoke instead of the AI."""
    async def go():
        try:
            return await coro
        except StopResponse:
            return "stopped"
        finally:
            if agent._end_task is not None:
                await agent._end_task
    return asyncio.run(go())


def agent_said(agent, text):
    async def go():
        ctx = agent.chat_ctx.copy()
        ctx.add_message(role="assistant", content=text)
        await agent.update_chat_ctx(ctx)
    asyncio.run(go())


def away(agent, state="away"):
    async def go():
        agent.on_user_state(state)
        if agent._end_task is not None:
            await agent._end_task
    asyncio.run(go())


def promise_made(call):
    call.report_person("customer")
    assert call.record_promise_to_pay(30, 10, 2026)["saved"] is True


SENT_CLOSE = "Sunita जी, link भेज दिया गया है। ₹2,200 का payment 30 October तक कर दीजिए। धन्यवाद, आपका दिन शुभ हो।"
DECLINED_CLOSE = "ठीक है Sunita जी। ₹2,200 का payment 30 October तक कर दीजिए। धन्यवाद, आपका दिन शुभ हो।"
DOB_HI = "Sunita जी, verification के लिए कृपया अपनी date of birth बताइए, दिन, महीना और साल।"


# item 1: a promise ends the payment push

def test_link_after_a_promise_closes_the_call_with_a_fixed_line(caplog):
    agent, call, store, session = make_agent()
    promise_made(call)
    call.heard("WhatsApp pe bhej do")
    with caplog.at_level(logging.INFO, logger="tam-voice-agent"):
        assert run_tool(agent, agent.send_payment_link(None, "whatsapp")) == "stopped"
    assert session.said == [SENT_CLOSE]
    assert session.closed is True and call.outcome == "promise_to_pay"
    assert "RULE promise_close" in caplog.text


def test_link_without_a_promise_goes_back_to_the_ai():
    agent, call, store, session = make_agent()
    call.report_person("customer")
    call.heard("SMS")
    assert run_tool(agent, agent.send_payment_link(None, "sms"))["sent"] is True
    assert session.said == [] and session.closed is False


def test_declining_the_link_after_a_promise_closes_the_call():
    agent, call, store, session = make_agent()
    promise_made(call)
    agent_said(agent, "क्या मैं आपको payment link भेज दूँ?")
    assert hear(agent, "नहीं, ज़रूरत नहीं") is True
    assert session.said == [DECLINED_CLOSE]
    assert session.closed is True and call.outcome == "promise_to_pay"


def test_okay_to_the_link_offer_goes_to_the_ai():
    agent, call, store, session = make_agent()
    promise_made(call)
    agent_said(agent, "क्या मैं आपको payment link भेज दूँ?")
    assert hear(agent, "Okay") is False
    assert session.said == []


def test_no_to_another_question_is_not_a_link_decline():
    agent, call, store, session = make_agent()
    promise_made(call)
    agent_said(agent, "क्या आपको कुछ और पूछना है?")
    assert hear(agent, "नहीं") is False


def test_english_call_gets_the_english_close():
    agent, call, store, session = make_agent()
    agent.language.current = "en"
    promise_made(call)
    call.heard("send it on SMS")
    run_tool(agent, agent.send_payment_link(None, "sms"))
    assert session.said == ["Sunita, the link has been sent. Please pay ₹2,200 by 30 October. "
                            "Thank you, have a good day."]


def test_handover_after_a_promise_is_refused_through_the_agent(caplog):
    agent, call, store, session = make_agent()
    promise_made(call)
    with caplog.at_level(logging.INFO, logger="tam-voice-agent"):
        r = run_tool(agent, agent.transfer_to_human(None, "customer_upset"))
    assert r["reason"] == "promise_already_recorded"
    assert "RULE transfer_to_human_refused: promise_already_recorded" in caplog.text


# item 4: the DOB question comes from the code

def test_dob_question_is_spoken_by_code(monkeypatch):
    monkeypatch.setattr(policy, "VERIFICATION_MODE", "name_plus_dob")
    agent, call, store, session = make_agent()
    assert run_tool(agent, agent.report_person(None, "customer")) == "stopped"
    assert session.said == [DOB_HI] and session.closed is False


def test_dob_question_when_the_caller_has_not_said_a_date(monkeypatch):
    monkeypatch.setattr(policy, "VERIFICATION_MODE", "name_plus_dob")
    agent, call, store, session = make_agent()
    call.report_person("customer")
    call.heard("haan bolo")
    assert run_tool(agent, agent.verify_identity(None, 2, 11, 1985)) == "stopped"
    assert session.said == [DOB_HI] and call.dob_attempts == 0


def test_impossible_date_is_asked_again_by_code(monkeypatch):
    monkeypatch.setattr(policy, "VERIFICATION_MODE", "name_plus_dob")
    agent, call, store, session = make_agent()
    call.report_person("customer")
    call.heard("31 February 1985")
    assert run_tool(agent, agent.verify_identity(None, 31, 2, 1985)) == "stopped"
    assert session.said == [caller_words.line(caller_words.DOB_RETRY, "hi", name="Sunita")]


def test_right_dob_goes_back_to_the_ai_with_the_reminder(monkeypatch):
    monkeypatch.setattr(policy, "VERIFICATION_MODE", "name_plus_dob")
    agent, call, store, session = make_agent()
    call.report_person("customer")
    call.heard("2 November 1985")
    r = run_tool(agent, agent.verify_identity(None, 2, 11, 1985))
    assert r["verified"] is True and r["next"] == "give_reminder" and session.said == []


def test_guard_drop_for_a_customer_owing_a_dob_gives_the_dob_question(monkeypatch):
    monkeypatch.setattr(policy, "VERIFICATION_MODE", "name_plus_dob")
    agent, call, store, session = make_agent()
    call.report_person("customer")

    async def leaky(self, chat_ctx, tools, model_settings):
        yield "आपकी EMI ₹2,200 की है।"
    monkeypatch.setattr(main.Agent.default, "llm_node", leaky)

    async def go():
        return "".join([c async for c in agent.llm_node(None, [], None) if isinstance(c, str)])
    assert asyncio.run(go()) == DOB_HI


def test_too_many_wrong_dobs_close_the_call(monkeypatch, caplog):
    monkeypatch.setattr(policy, "VERIFICATION_MODE", "name_plus_dob")
    agent, call, store, session = make_agent()
    call.report_person("customer")
    results = []
    with caplog.at_level(logging.INFO, logger="tam-voice-agent"):
        for _ in range(policy.MAX_DOB_ATTEMPTS):
            call.heard("15 March 1990")
            results.append(run_tool(agent, agent.verify_identity(None, 15, 3, 1990)))
    assert results == ["stopped"] * 3  # the code speaks after every wrong date
    retry = caller_words.line(caller_words.DOB_RETRY, "hi", name="Sunita")
    assert session.said == [retry, retry, caller_words.DOB_FAILED_CLOSE["hi"]]
    assert "RULE dob_retry" in caplog.text
    assert session.closed is True and call.unlocked is False
    assert call.outcome == "human_callback" and len(store.callbacks_due(FAR)) == 1
    assert "RULE dob_failed_close" in caplog.text


def test_sister_who_says_she_is_sunita_must_pass_the_dob_and_fails():
    agent, call, store, session = make_agent()  # name_only mode: the correction still needs a DOB
    assert hear(agent, "main unki behen hoon, kya kaam hai?") is True
    assert hear(agent, "actually main hi Sunita hoon") is False
    assert run_tool(agent, agent.report_person(None, "customer")) == "stopped"
    assert session.said[-1] == DOB_HI and call.unlocked is False
    for _ in range(policy.MAX_DOB_ATTEMPTS):
        assert hear(agent, "15 March 1990") is False
        assert run_tool(agent, agent.verify_identity(None, 15, 3, 1990)) == "stopped"
    assert session.said[-1] == caller_words.DOB_FAILED_CLOSE["hi"]
    assert not any("सहयोगी" in s for s in session.said)  # no colleague promised to her
    assert "2,200" not in " ".join(session.said)  # nothing about the loan reached her
    assert session.closed is True and call.outcome == "human_callback"


# item 5: silence

def test_first_silence_checks_in_and_the_second_ends_the_call(caplog):
    agent, call, store, session = make_agent()
    with caplog.at_level(logging.INFO, logger="tam-voice-agent"):
        away(agent)
        assert session.said == ["क्या आप मुझे सुन पा रहे हैं?"] and session.closed is False
        assert session.away_resets == 1  # so LiveKit can mark the caller away a second time
        away(agent)
    assert session.said[-1] == caller_words.SILENCE_CLOSE["hi"]
    assert session.closed is True
    assert call.outcome == "no_resolution" and call.note == "customer silent"
    assert "RULE silence_check_in" in caplog.text and "RULE silence_close" in caplog.text


def test_speaking_resets_the_silence_count():
    agent, call, store, session = make_agent()
    away(agent)
    away(agent, "speaking")
    away(agent)
    assert session.said == [caller_words.SILENCE_CHECK["hi"]] * 2 and session.closed is False


def test_a_finished_message_resets_the_silence_count():
    agent, call, store, session = make_agent()
    away(agent)
    hear(agent, "हाँ, मैं सुन रही हूँ")
    away(agent)
    assert session.closed is False


def test_silence_in_an_english_call_uses_english():
    agent, call, store, session = make_agent()
    agent.language.current = "en"
    away(agent)
    away(agent)
    assert session.said == ["Can you hear me?", caller_words.SILENCE_CLOSE["en"]]


def test_silence_after_a_promise_keeps_the_promise_outcome():
    agent, call, store, session = make_agent()
    promise_made(call)
    away(agent)
    away(agent)
    assert call.outcome == "promise_to_pay"
