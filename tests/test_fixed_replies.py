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

from tests.helpers import confirmed  # noqa: E402
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
    confirmed(call)
    with caplog.at_level(logging.WARNING, logger="tam-voice-agent"):
        assert hear(agent, "case hoga kya?") is True
    assert "RULE legal_handoff" in caplog.text
    assert session.said == [caller_words.LEGAL_LINE["hi"]]
    assert session.closed is True
    assert len(store.callbacks_due(FAR)) == 1
    assert call.outcome == "human_callback" and call.handoff_requested


def test_legal_question_keeps_one_callback_even_after_an_earlier_handover():
    agent, call, store, session = make_agent()
    confirmed(call)
    call.transfer_to_human("hardship")
    hear(agent, "court jaana padega kya?")
    assert len(store.callbacks_due(FAR)) == 1


def test_legal_words_before_identity_do_not_hand_over():
    agent, call, store, session = make_agent()
    assert hear(agent, "case hai kya koi?") is False
    assert call.handoff_requested is False and store.callbacks_due(FAR) == []


def test_ordinary_answer_goes_to_the_ai():
    agent, call, store, session = make_agent()
    confirmed(call)
    assert hear(agent, "शुक्रवार को दे दूँगी") is False
    assert session.said == [] and call.caller_lines[-1] == "शुक्रवार को दे दूँगी"


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
    confirmed(call)
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
    confirmed(call)
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
    confirmed(call)
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
    call.heard("haan ji")
    assert run_tool(agent, agent.report_person(None, "customer")) == "stopped"
    assert session.said == [DOB_HI] and session.closed is False


def test_dob_question_when_the_caller_has_not_said_a_date(monkeypatch):
    monkeypatch.setattr(policy, "VERIFICATION_MODE", "name_plus_dob")
    agent, call, store, session = make_agent()
    confirmed(call)
    call.heard("haan bolo")
    assert run_tool(agent, agent.verify_identity(None, 2, 11, 1985)) == "stopped"
    assert session.said == [DOB_HI] and call.dob_attempts == 0


def test_impossible_date_is_asked_again_by_code(monkeypatch):
    monkeypatch.setattr(policy, "VERIFICATION_MODE", "name_plus_dob")
    agent, call, store, session = make_agent()
    confirmed(call)
    call.heard("31 February 1985")
    assert run_tool(agent, agent.verify_identity(None, 31, 2, 1985)) == "stopped"
    assert session.said == [caller_words.line(caller_words.DOB_RETRY, "hi", name="Sunita")]


def test_right_dob_goes_back_to_the_ai_with_the_reminder(monkeypatch):
    monkeypatch.setattr(policy, "VERIFICATION_MODE", "name_plus_dob")
    agent, call, store, session = make_agent()
    confirmed(call)
    call.heard("2 November 1985")
    r = run_tool(agent, agent.verify_identity(None, 2, 11, 1985))
    assert r["verified"] is True and r["next"] == "give_reminder" and session.said == []


def test_guard_drop_for_a_customer_owing_a_dob_gives_the_dob_question(monkeypatch):
    monkeypatch.setattr(policy, "VERIFICATION_MODE", "name_plus_dob")
    agent, call, store, session = make_agent()
    confirmed(call)

    async def leaky(self, chat_ctx, tools, model_settings):
        yield "आपकी EMI ₹2,200 की है।"
    monkeypatch.setattr(main.Agent.default, "llm_node", leaky)

    async def go():
        return "".join([c async for c in agent.llm_node(None, [], None) if isinstance(c, str)])
    assert asyncio.run(go()) == DOB_HI


def test_too_many_wrong_dobs_close_the_call(monkeypatch, caplog):
    monkeypatch.setattr(policy, "VERIFICATION_MODE", "name_plus_dob")
    agent, call, store, session = make_agent()
    confirmed(call)
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


# ---- batch 3 ----

def reply_to(agent, monkeypatch, text):
    """What the guard lets through when the AI writes `text`."""
    async def ai(self, chat_ctx, tools, model_settings):
        yield text
    monkeypatch.setattr(main.Agent.default, "llm_node", ai)

    async def go():
        return "".join([c async for c in agent.llm_node(None, [], None) if isinstance(c, str)])
    return asyncio.run(go())


GREETING_AGAIN = "नमस्ते Sunita जी, मैं Tam Infosoft की AI असिस्टेंट बोल रही हूँ।"


def test_bare_okay_gets_the_short_identity_question(caplog):
    agent, call, store, session = make_agent()
    call.heard("Okay")
    with caplog.at_level(logging.INFO, logger="tam-voice-agent"):
        assert run_tool(agent, agent.report_person(None, "customer")) == "stopped"
    assert session.said == ["क्या मेरी बात Sunita जी से हो रही है?"]
    assert call.unlocked is False and "RULE identity_not_confirmed" in caplog.text


def test_english_call_gets_the_english_identity_question():
    agent, call, store, session = make_agent()
    agent.language.current = "en"
    call.heard("okay")
    run_tool(agent, agent.report_person(None, "customer"))
    assert session.said == ["Am I speaking with Sunita?"]


def test_repeated_greeting_is_dropped_and_the_rest_kept(monkeypatch):
    agent, call, store, session = make_agent()
    confirmed(call)
    assert reply_to(agent, monkeypatch, GREETING_AGAIN + " आप इसे कब pay कर पाएंगे?") == \
        "आप इसे कब pay कर पाएंगे? "


def test_only_a_greeting_before_identity_asks_who_it_is(monkeypatch):
    agent, call, store, session = make_agent()
    assert reply_to(agent, monkeypatch, GREETING_AGAIN) == "क्या मेरी बात Sunita जी से हो रही है?"


def test_only_a_greeting_after_identity_gives_the_reminder_again(monkeypatch):
    agent, call, store, session = make_agent()
    confirmed(call)
    assert reply_to(agent, monkeypatch, "नमस्ते Sunita जी।") == \
        "आपकी ₹2,200 की EMI 10 September को due थी। आप इसे कब pay कर पाएंगे?"


def test_only_a_greeting_while_owing_a_dob_asks_for_it(monkeypatch):
    monkeypatch.setattr(policy, "VERIFICATION_MODE", "name_plus_dob")
    agent, call, store, session = make_agent()
    confirmed(call)
    assert reply_to(agent, monkeypatch, GREETING_AGAIN) == DOB_HI


def turn_with_ctx(agent, text):
    """Runs the turn hook with a real context; returns the system lines it added."""
    ctx = llm.ChatContext.empty()

    async def go():
        await agent.on_user_turn_completed(ctx, llm.ChatMessage(role="user", content=[text]))
    asyncio.run(go())
    return [m.text_content for m in ctx.items if m.type == "message" and m.role == "system"]


def test_weekday_date_is_added_to_the_turn(caplog):
    agent, call, store, session = make_agent()  # today is Wednesday 28 October
    confirmed(call)
    with caplog.at_level(logging.INFO, logger="tam-voice-agent"):
        added = turn_with_ctx(agent, "is Friday kar dungi")
    assert added == ["The caller named these days (IST): Friday = Friday 30 October. Use exactly these dates."]
    assert "RULE weekday_resolved: Friday = Friday 30 October" in caplog.text


def test_turn_without_a_day_adds_nothing():
    agent, call, store, session = make_agent()
    confirmed(call)
    assert turn_with_ctx(agent, "haan ji") == []


def test_agreed_date_and_link_request_waits_then_sends_and_closes(caplog):
    agent, call, store, session = make_agent()
    confirmed(call)
    agent_said(agent, "ठीक है, शुक्रवार 30 October को pay करेंगे, सही?")
    with caplog.at_level(logging.INFO, logger="tam-voice-agent"):
        assert hear(agent, "हाँ, WhatsApp पे भेज दो") is False
        refused = run_tool(agent, agent.send_payment_link(None, "whatsapp"))
        assert refused["reason"] == "record_promise_first" and store.latest_link("C002") is None
        assert run_tool(agent, agent.record_promise_to_pay(None, 30, 10, 2026)) == "stopped"
    assert store.latest_link("C002")["channel"] == "whatsapp"
    assert session.said == [SENT_CLOSE]
    assert session.closed is True and call.outcome == "promise_to_pay"
    for rule in ("RULE promise_date_agreed", "RULE record_promise_first", "RULE link_after_promise",
                 "RULE promise_close"):
        assert rule in caplog.text


def test_promise_without_a_waiting_link_goes_back_to_the_ai():
    agent, call, store, session = make_agent()
    confirmed(call)
    r = run_tool(agent, agent.record_promise_to_pay(None, 30, 10, 2026))
    assert r["saved"] is True and session.said == [] and store.latest_link("C002") is None


def test_loan_talk_before_identity_falls_back_to_the_identity_question(monkeypatch):
    # found in a real nano run: greeting + EMI before report_person, both dropped
    agent, call, store, session = make_agent()
    out = reply_to(agent, monkeypatch, GREETING_AGAIN + " आपकी EMI ₹2,200 की due थी 10 September।")
    assert out == "क्या मेरी बात Sunita जी से हो रही है?"


def test_third_party_still_hears_try_them_later(monkeypatch):
    agent, call, store, session = make_agent()
    call.report_person("third_party")
    assert reply_to(agent, monkeypatch, "आपकी EMI ₹2,200 है।") == \
        "मैं उनसे बाद में फिर से बात करने की कोशिश करूँगी।"


@pytest.mark.parametrize("text", ["nahi, kal hi kar dungi", "नहीं, 2 November को", "no, Monday"])
def test_no_with_a_new_day_is_not_a_link_decline(text):
    # found in a real nano run: the call closed on the old date
    agent, call, store, session = make_agent()
    promise_made(call)
    agent_said(agent, "क्या मैं आपको payment link भेज दूँ?")
    assert hear(agent, text) is False
    assert session.said == [] and session.closed is False


def test_a_new_day_after_a_promise_tells_the_ai_it_can_change_it():
    # found in a real nano run: "nahi, kal hi" after a 30 October promise kept the old date
    agent, call, store, session = make_agent()
    promise_made(call)  # 30 October
    added = turn_with_ctx(agent, "nahi, kal hi kar dungi")
    assert added[1] == ("The saved promise is Friday 30 October, but the caller named Thursday "
                        "29 October. If they are changing the promise date, call "
                        "record_promise_to_pay with the new date; it replaces the old one.")


def test_the_same_day_as_the_promise_adds_no_change_note():
    agent, call, store, session = make_agent()
    promise_made(call)
    assert len(turn_with_ctx(agent, "haan, Friday pakka")) == 1


def test_a_clear_yes_before_identity_tells_the_ai_to_report_it():
    agent, call, store, session = make_agent()
    assert turn_with_ctx(agent, "हाँ जी, बोलिए") == [
        "The caller just confirmed they are Sunita. Call report_person with status customer now."]
    assert turn_with_ctx(agent, "Okay") == []
