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


# ---- batch 4 ----

def hardship_handed_over(agent, call):
    confirmed(call)
    call.heard("job chali gayi, abhi paise nahi hain")
    assert run_tool(agent, agent.transfer_to_human(None, "hardship"))["requested"] is True


def test_after_hardship_the_guard_drops_pay_asks_and_asks_for_a_time(monkeypatch):
    agent, call, store, session = make_agent()
    hardship_handed_over(agent, call)
    assert reply_to(agent, monkeypatch, "मैं समझ सकती हूँ। आप इसे कब pay कर पाएंगे?") == "मैं समझ सकती हूँ। "
    assert reply_to(agent, monkeypatch, "आप इसे कब pay कर पाएंगे?") == \
        "हमारी सहयोगी आपको किस दिन और किस समय कॉल करें?"


def test_callback_time_after_hardship_closes_the_call(caplog):
    agent, call, store, session = make_agent()
    hardship_handed_over(agent, call)
    call.heard("Monday 12 baje")
    with caplog.at_level(logging.INFO, logger="tam-voice-agent"):
        assert run_tool(agent, agent.schedule_callback(None, 2, 11, 2026, 12, 0, "customer_requested")) == "stopped"
    assert session.said == ["ठीक है Sunita जी, हमारी सहयोगी आपको Monday 2 November, दोपहर 12 बजे कॉल करेंगी। "
                            "धन्यवाद, अपना ध्यान रखिए।"]
    assert session.closed is True and len(store.callbacks_due(FAR)) == 1
    assert "RULE handoff_close" in caplog.text and "RULE one_callback_per_handoff" in caplog.text


def test_english_handoff_close():
    agent, call, store, session = make_agent()
    agent.language.current = "en"
    hardship_handed_over(agent, call)
    call.heard("Tuesday 4:30 pm")
    run_tool(agent, agent.schedule_callback(None, 3, 11, 2026, 16, 30, "customer_requested"))
    assert session.said == ["All right, Sunita. Our colleague will call you on Tuesday 3 November at 4:30 pm. "
                            "Thank you, take care."]


def test_callback_without_hardship_goes_back_to_the_ai():
    agent, call, store, session = make_agent()
    confirmed(call)
    call.heard("kal 4 baje call karna")
    r = run_tool(agent, agent.schedule_callback(None, 29, 10, 2026, 16, 0, "customer_requested"))
    assert r["saved"] is True and session.said == [] and session.closed is False


# ---- batch 4 combined: identity by code ----

DUE_HI = "आपकी ₹2,200 की EMI 10 September को due थी। आप इसे कब pay कर पाएंगे?"
CONFIRM_HI = "क्या मेरी बात Sunita जी से हो रही है?"


def says(lines, **values):
    return caller_words.line(lines, "hi", name="Sunita", **values)


@pytest.mark.parametrize("text", ["हाँ जी, बोलिए", "haan ji", "सुनीता बोल रही हूँ", "Suneeta speaking",
                                  "ji, main hi hoon"])
def test_a_clear_yes_unlocks_and_the_code_gives_the_reminder(text, caplog):
    agent, call, store, session = make_agent()
    with caplog.at_level(logging.INFO, logger="tam-voice-agent"):
        assert hear(agent, text) is True
    assert call.unlocked and call.dob_verified is False and session.said == [DUE_HI]
    assert "RULE identity_confirmed_by_code" in caplog.text


def test_okay_at_the_greeting_asks_who_it_is_again(caplog):
    agent, call, store, session = make_agent()
    with caplog.at_level(logging.INFO, logger="tam-voice-agent"):
        assert hear(agent, "Okay") is True
    assert session.said == [CONFIRM_HI] and call.person_status is None
    assert "RULE identity_not_confirmed" in caplog.text


def test_a_busy_yes_is_left_to_the_ai():
    agent, call, store, session = make_agent()
    assert turn_with_ctx(agent, "haan main hi hoon, par abhi busy hoon") == [
        "The caller just confirmed they are Sunita. Call report_person with status customer now."]
    assert session.said == []


def test_closed_loan_ends_with_a_fixed_line():
    agent, call, store, session = make_agent("C009")
    assert hear(agent, "haan ji") is True
    assert session.said == [caller_words.LOAN_CLOSED["hi"]] and session.closed is True


def test_ai_reporting_the_customer_gets_the_reminder_from_code():
    agent, call, store, session = make_agent()
    call.heard("haan, main hi Sunita")
    assert run_tool(agent, agent.report_person(None, "customer")) == "stopped"
    assert session.said == [DUE_HI]


# ---- batch 4 combined: someone else answers ----

BROTHER = "nahi, main unka bhai Rahul bol raha hoon"


def verified_brother(agent):
    assert hear(agent, BROTHER) is True
    assert hear(agent, "haan bataiye") is True
    assert hear(agent, "2 November 1985") is True


def test_brother_who_agrees_and_knows_the_dob_hears_about_the_emi(caplog):
    agent, call, store, session = make_agent()
    with caplog.at_level(logging.INFO, logger="tam-voice-agent"):
        verified_brother(agent)
    assert session.said == [
        says(caller_words.TP_CONSENT), says(caller_words.TP_DOB),
        says(caller_words.TP_REMINDER, amount="2,200", date="10 September")]
    assert call.unlocked and call.person_status == "third_party"
    assert call.summary()["third_party"] == {
        "mode": "dob_verified", "name": "Rahul", "relationship": "brother",
        "consent": "yes", "dob": "verified", "shared": True}
    assert ("RULE third_party_disclosure: mode=dob_verified, relationship=brother, consent=yes, "
            "dob=verified, shared=True") in caplog.text
    assert "# Speaking with" in agent.instructions


def test_verified_brother_promise_is_noted_and_the_link_goes_to_her_number():
    agent, call, store, session = make_agent()
    verified_brother(agent)
    call.heard("woh Friday 30 October tak kar degi")
    assert run_tool(agent, agent.record_promise_to_pay(None, 30, 10, 2026))["saved"] is True
    assert store.promises_for("C002", "open")[0]["note"] == "promised by brother Rahul"
    assert call.summary()["promised_by"] == "brother Rahul"
    call.heard("SMS pe bhej do")
    assert run_tool(agent, agent.send_payment_link(None, "sms")) == "stopped"
    assert session.said[-1] == says(caller_words.PROMISE_CLOSE_SENT_TP, amount="2,200", date="30 October")
    assert session.closed is True


def test_verified_brother_link_without_a_promise_names_her_number():
    agent, call, store, session = make_agent()
    verified_brother(agent)
    call.heard("WhatsApp pe bhej do")
    assert run_tool(agent, agent.send_payment_link(None, "whatsapp")) == "stopped"
    assert session.said[-1] == says(caller_words.TP_LINK_SENT, digits="0002")


def test_guard_speaks_about_her_in_the_third_person(monkeypatch):
    agent, call, store, session = make_agent()
    verified_brother(agent)
    assert reply_to(agent, monkeypatch, "आपकी ₹2,200 की EMI pending है।") == "Sunita जी की ₹2,200 की EMI pending है। "


def test_brother_with_the_wrong_dob_three_times_hears_nothing(caplog):
    agent, call, store, session = make_agent()
    with caplog.at_level(logging.INFO, logger="tam-voice-agent"):
        hear(agent, BROTHER)
        hear(agent, "haan bataiye")
        for _ in range(3):
            assert hear(agent, "15 March 1990") is True
        assert hear(agent, "kal shaam 5 baje") is True
    retry = says(caller_words.TP_DOB_RETRY)
    assert session.said[2:] == [retry, retry, says(caller_words.TP_DOB_FAILED),
                                says(caller_words.TP_CLOSE_TIME, when="Thursday 29 October, शाम 5 बजे")]
    assert "2,200" not in " ".join(session.said) and call.unlocked is False
    assert session.closed is True and call.outcome == "third_party"
    cbs = store.callbacks_due(FAR)
    assert len(cbs) == 1 and cbs[0]["reason"] == "customer_unavailable"
    assert cbs[0]["due_at"] == datetime(2026, 10, 29, 17, 0, tzinfo=IST)
    assert "dob=failed, shared=False" in caplog.text


def test_brother_who_says_no_hears_nothing_and_gives_a_time():
    agent, call, store, session = make_agent()
    hear(agent, BROTHER)
    assert hear(agent, "nahi, aap unse hi baat kariye") is True
    assert session.said[-1] == says(caller_words.TP_NO_SHARE)
    assert hear(agent, "Monday 12 baje") is True
    assert session.said[-1] == says(caller_words.TP_CLOSE_TIME, when="Monday 2 November, दोपहर 12 बजे")
    assert call.third_party["consent"] == "no" and session.closed is True


def test_unclear_consent_is_asked_once_more_then_nothing_is_shared():
    agent, call, store, session = make_agent()
    hear(agent, BROTHER)
    hear(agent, "hmm")
    assert session.said[-1] == says(caller_words.TP_CONSENT_AGAIN)
    hear(agent, "hmm")
    assert session.said[-1] == says(caller_words.TP_NO_SHARE)
    assert call.third_party["consent"] == "unclear"


def test_relationship_is_asked_when_not_given():
    agent, call, store, session = make_agent()
    assert hear(agent, "Sunita ghar pe nahi hai") is True
    assert session.said == [says(caller_words.TP_RELATIONSHIP)]
    hear(agent, "main unki padosi hoon")
    assert call.third_party["relationship"] == "neighbour"
    assert session.said[-1] == says(caller_words.TP_CONSENT)


def test_mode_none_shares_nothing(monkeypatch):
    monkeypatch.setattr(policy, "THIRD_PARTY_DISCLOSURE", "none")
    agent, call, store, session = make_agent()
    assert hear(agent, BROTHER) is True
    assert session.said == [says(caller_words.TP_NO_SHARE)]
    assert call.third_party["consent"] == "not_asked"


def test_sister_who_says_she_is_sunita_gets_the_dob_question(caplog):
    agent, call, store, session = make_agent()
    with caplog.at_level(logging.INFO, logger="tam-voice-agent"):
        hear(agent, "main unki behen bol rahi hoon")
        assert hear(agent, "actually main hi Sunita hoon") is True
    assert session.said[-1] == DOB_HI and call.unlocked is False
    assert "RULE third_party_claims_customer" in caplog.text
    assert hear(agent, "2 November 1985") is True
    assert session.said[-1] == "धन्यवाद, verification हो गया। " + DUE_HI
    assert call.person_status == "customer" and call.unlocked


def test_wrong_number_closes_politely():
    agent, call, store, session = make_agent()
    assert hear(agent, "wrong number hai") is True
    assert session.said == [caller_words.WRONG_NUMBER_CLOSE["hi"]] and session.closed is True
    assert call.outcome == "wrong_number"


def test_asking_why_during_the_check_gets_the_personal_line():
    agent, call, store, session = make_agent()
    hear(agent, BROTHER)
    hear(agent, "pehle batao kya baat hai")
    assert session.said[-1] == says(caller_words.TP_PERSONAL) + " " + says(caller_words.TP_CONSENT)


def test_language_does_not_switch_while_waiting_for_a_dob():
    agent, call, store, session = make_agent()
    hear(agent, BROTHER)
    hear(agent, "haan bataiye")
    hear(agent, "it is the fifteenth of March nineteen ninety")
    hear(agent, "sorry, it is the sixteenth of March nineteen ninety")
    assert agent.language.current == "hi"


def test_ai_reporting_a_third_party_hands_over_to_the_code():
    agent, call, store, session = make_agent()
    call.heard("I am not the person you are looking for")
    assert run_tool(agent, agent.report_person(None, "third_party")) == "stopped"
    assert session.said == [says(caller_words.TP_RELATIONSHIP)]
    assert agent.flow.active


def test_money_stays_blocked_until_the_check_passes(monkeypatch):
    agent, call, store, session = make_agent()
    hear(agent, BROTHER)
    assert "2,200" not in reply_to(agent, monkeypatch, "उनकी EMI ₹2,200 है।")


# ---- batch 4 combined: hardship read by the code ----

def test_hardship_with_a_date_goes_to_the_promise_and_no_colleague(monkeypatch, caplog):
    agent, call, store, session = make_agent()
    confirmed(call)
    with caplog.at_level(logging.INFO, logger="tam-voice-agent"):
        assert hear(agent, "job chali gayi hai, par Friday tak kar dungi") is False
    assert call.hardship == "with_date" and "RULE hardship_with_date" in caplog.text
    r = run_tool(agent, agent.transfer_to_human(None, "hardship"))
    assert r["requested"] is False and r["reason"] == "hardship_with_a_pay_date"
    assert reply_to(agent, monkeypatch, "ठीक है। हमारी सहयोगी आपको वापस कॉल करेंगी।") == "ठीक है। "


def test_hardship_without_a_date_asks_once_then_books_the_colleague(caplog):
    agent, call, store, session = make_agent()
    confirmed(call)
    with caplog.at_level(logging.INFO, logger="tam-voice-agent"):
        assert hear(agent, "abhi paise nahi hain, meri job chali gayi") is True
        assert session.said == [says(caller_words.HARDSHIP_DATE_ASK)]
        assert hear(agent, "abhi kuch nahi keh sakti") is True
        assert session.said[-1] == says(caller_words.HARDSHIP_CALLBACK)
        assert hear(agent, "Monday 12 baje") is True
    assert session.said[-1] == says(caller_words.HANDOFF_CLOSE, when="Monday 2 November, दोपहर 12 बजे")
    cbs = store.callbacks_due(FAR)
    assert len(cbs) == 1 and cbs[0]["reason"] == "human_followup"
    assert cbs[0]["due_at"] == datetime(2026, 11, 2, 12, 0, tzinfo=IST)
    assert call.outcome == "hardship" and session.closed is True
    assert "RULE hardship_no_date" in caplog.text and "RULE handoff_close" in caplog.text


def test_hardship_then_a_date_after_the_one_ask():
    agent, call, store, session = make_agent()
    confirmed(call)
    hear(agent, "salary nahi aayi abhi")
    assert hear(agent, "5 tareekh ko kar dungi") is False
    assert call.hardship == "with_date" and call.push_stopped is None


def test_hardship_without_a_time_closes_after_two_tries():
    agent, call, store, session = make_agent()
    confirmed(call)
    hear(agent, "medical emergency hai, paise nahi hain")
    hear(agent, "pata nahi")
    hear(agent, "dekhte hain")
    assert session.said[-1] == caller_words.TIME_INCOMPLETE["hi"]
    hear(agent, "kabhi bhi")
    assert session.said[-1] == says(caller_words.HANDOFF_CLOSE_NO_TIME) and session.closed is True


def test_after_hardship_without_a_date_the_guard_drops_pay_asks(monkeypatch):
    agent, call, store, session = make_agent()
    confirmed(call)
    hear(agent, "paise nahi hain")
    hear(agent, "nahi pata")
    assert reply_to(agent, monkeypatch, "आप कब तक pay कर पाएंगे?") == caller_words.CALLBACK_TIME_QUESTION["hi"]


@pytest.mark.parametrize("text", ["kya kanooni karwai hogi?", "legal notice bhejoge kya?",
                                  "कानूनी कार्रवाई होगी क्या?", "will there be prosecution?",
                                  "summons aayega kya?"])
def test_new_legal_words_hand_over(text):
    agent, call, store, session = make_agent()
    confirmed(call)
    assert hear(agent, text) is True
    assert session.said == [caller_words.LEGAL_LINE["hi"]] and session.closed is True


def test_outcome_after_a_promise_closes_the_call_with_the_fixed_line(caplog):
    # gpt-5.4-mini recorded the outcome and said its own goodbye, and the call stayed open
    agent, call, store, session = make_agent()
    promise_made(call)
    with caplog.at_level(logging.INFO, logger="tam-voice-agent"):
        assert run_tool(agent, agent.record_outcome(None, "promise_to_pay")) == "stopped"
    assert session.said == [DECLINED_CLOSE] and session.closed is True
    assert "RULE promise_close: outcome recorded" in caplog.text


def test_outcome_without_a_promise_goes_back_to_the_ai():
    agent, call, store, session = make_agent()
    confirmed(call)
    assert run_tool(agent, agent.record_outcome(None, "no_resolution"))["saved"] is True
    assert session.said == [] and session.closed is False
