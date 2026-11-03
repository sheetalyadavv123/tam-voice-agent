"""Batch 6 fixes from a Console test: a busy caller before any yes, "I'm" is not a yes,
"any time" with a day books at once, the time is asked once, no colleague for a busy
customer."""
import logging
from datetime import datetime

import pytest

from tests.test_fixed_replies import (  # noqa: F401  (fake_session is an autouse fixture)
    CONFIRM_HI, FAR, fake_session, hear, make_agent, run_tool, says,
)
from tools import caller_words
from tools.call_later import asks_call_later, busy_before_identity
from tools.rules import IST
from tools.speech_guard import ReplyGuard

MONDAY_11 = datetime(2026, 11, 2, 11, 0, tzinfo=IST)  # NOW is Wednesday 28 October
THURSDAY_11 = datetime(2026, 10, 29, 11, 0, tzinfo=IST)
COLLEAGUE_WORDS = ("सहयोगी", "colleague")

# The Console test, word for word
TRANSCRIPT_FIRST = "Actually, I'm quite occupied. Right? Now. So can you please call me on Monday?"
TRANSCRIPT_SECOND = ["You can call me whenever", "किसी भी time call कर लेना Monday"]


def _callbacks(store):
    return [(c["reason"], c["due_at"], c["note"]) for c in store.callbacks_due(FAR)]


# ---- "I'm" is not a yes ----

@pytest.mark.parametrize("text", [
    "I'm busy", "I'm occupied", "I'm driving", "I am in a meeting", "I'm quite occupied",
    "I am", "I'm", "this is", TRANSCRIPT_FIRST, "Im busy right now", "I'm not free"])
def test_im_without_the_name_is_not_a_yes(text):
    assert caller_words.confirms_identity(text, "Sunita") is False


@pytest.mark.parametrize("text", ["I'm Sunita", "I am Sunita", "this is Sunita", "I'm Suneeta",
                                  "I am सुनीता", "this is Sunita Verma"])
def test_im_with_the_name_is_a_yes(text):
    assert caller_words.confirms_identity(text, "Sunita") is True


def test_im_with_a_close_name_still_needs_a_yes():
    assert caller_words.confirms_identity("I'm Amita", "Amit") is False


# ---- the transcript ----

@pytest.mark.parametrize("second", TRANSCRIPT_SECOND)
def test_the_console_transcript_does_not_unlock_and_books_monday(second, caplog):
    agent, call, store, session = make_agent()
    with caplog.at_level(logging.INFO, logger="tam-voice-agent"):
        assert hear(agent, TRANSCRIPT_FIRST) is True  # the code, not the AI
        assert session.said == [CONFIRM_HI]  # the identity question, not the EMI reminder
        assert call.person_status is None and not call.unlocked
        assert hear(agent, second) is True
    close = session.said[-1]
    assert "Monday 2 November" in close and not any(w in close for w in COLLEAGUE_WORDS)
    assert "EMI" not in " ".join(session.said) and "₹" not in " ".join(session.said)
    assert len(session.said) == 2 and session.closed is True  # no question about the time
    assert not call.unlocked and call.person_status == "unavailable"
    assert _callbacks(store) == [("customer_unavailable", MONDAY_11, "caller said any time")]
    assert call.outcome == "not_available"
    assert call.note == "identity not confirmed, callback booked; caller said any time"
    assert [p for p in store.promises_for("C002") if p["session_id"] == "s1"] == []
    assert store.latest_link("C002") is None
    assert "RULE identity_busy_unconfirmed" in caplog.text
    assert "RULE identity_busy_answer" in caplog.text


def test_the_ai_cannot_unlock_after_im_busy():
    agent, call, store, session = make_agent()
    call.heard(TRANSCRIPT_FIRST)
    assert run_tool(agent, agent.report_person(None, "customer")) == "stopped"
    assert not call.unlocked and session.said == [CONFIRM_HI]


# ---- busy before identity ----

@pytest.mark.parametrize("text", [
    "I'm busy", "I'm occupied", "not a good time", "call me later", "call me tomorrow",
    "call me on Monday", "can you call back?", "could you call me later", "abhi time nahi hai",
    "abhi nahi", "baad mein", "अभी नहीं", "बाद में कॉल करना", "अभी टाइम नहीं है",
    "abhi busy hoon", "baad mein call karo", "Monday call karna"])
def test_busy_before_any_yes_asks_who_it_is_once(text):
    agent, call, store, session = make_agent()
    assert busy_before_identity(text, "Sunita")
    assert hear(agent, text) is True
    assert session.said == [CONFIRM_HI] and call.person_status is None


@pytest.mark.parametrize("text", [
    "Sunita abhi nahi hai", "wo abhi busy hai", "she is busy, call later", "main unki behen hoon",
    "haan, abhi busy hoon", "wrong number", "nahi"])
def test_not_busy_before_identity(text):
    assert not busy_before_identity(text, "Sunita")


def test_someone_else_saying_she_isnt_here_still_gets_the_third_party_flow():
    agent, call, store, session = make_agent()
    hear(agent, "Sunita abhi nahi hai")
    assert session.said == [says(caller_words.TP_RELATIONSHIP)]


def test_a_clear_yes_after_the_question_goes_into_the_call_later_flow():
    agent, call, store, session = make_agent()
    hear(agent, "call me on Monday")
    hear(agent, "haan ji")
    assert session.said[-1] == says(caller_words.TIME_FOR_DAY, day="Monday 2 November")
    assert agent.later.active and agent.later.confirmed and not call.unlocked
    hear(agent, "pata nahi")
    assert _callbacks(store) == [("customer_requested", MONDAY_11, "caller gave day only")]
    assert call.outcome == "callback_requested"


def test_no_yes_and_no_day_asks_the_day_and_books_for_the_customer():
    agent, call, store, session = make_agent()
    hear(agent, "I'm busy")
    hear(agent, "hmm")
    assert session.said[-1] == caller_words.CALL_LATER_ASK["hi"] and not agent.later.confirmed
    hear(agent, "kal")
    assert session.said[-1] == says(caller_words.TIME_FOR_DAY, day="Thursday 29 October")
    hear(agent, "dekhte hain")
    assert _callbacks(store) == [("customer_unavailable", THURSDAY_11, "caller gave day only")]
    assert not call.unlocked and call.outcome == "not_available"


def test_no_after_the_question_is_someone_else():
    agent, call, store, session = make_agent()
    hear(agent, "abhi busy hoon")
    hear(agent, "nahi, main unki behen hoon")
    assert call.person_status == "third_party" and agent.flow.active


def test_the_identity_question_is_asked_once():
    agent, call, store, session = make_agent()
    hear(agent, "I'm busy")
    hear(agent, "I'm still busy")
    assert session.said.count(CONFIRM_HI) == 1


def test_busy_yes_with_a_day_and_any_time_closes_at_once():
    agent, call, store, session = make_agent()
    assert hear(agent, "haan, abhi busy hoon, Monday kisi bhi time") is True
    assert session.said == [says(caller_words.CALL_LATER_CLOSE_DAY, day="Monday 2 November")]
    assert session.closed is True
    assert _callbacks(store) == [("customer_requested", MONDAY_11, "caller said any time")]
    assert call.outcome == "callback_requested" and call.note == "caller said any time"


# ---- more call-later phrases ----

@pytest.mark.parametrize("text", [
    "I'm busy", "I'm occupied", "not a good time", "call me later", "call me tomorrow",
    "call me on Monday", "can you call back", "abhi time nahi", "abhi time nahi hai"])
def test_call_later_phrases_mid_call(text):
    assert asks_call_later(text)


@pytest.mark.parametrize("text", ["abhi nahi", "baad mein", "अभी नहीं", "बाद में"])
def test_short_hindi_phrases_only_at_the_greeting(text):
    # mid-call they answer "when will you pay?"
    assert asks_call_later(text, greeting=True) and not asks_call_later(text)


def test_a_verified_customer_saying_abhi_nahi_is_left_to_the_ai():
    agent, call, store, session = make_agent()
    hear(agent, "haan ji")  # the reminder: "when will you pay?"
    assert hear(agent, "abhi nahi") is False and not agent.later.active


def test_a_verified_customer_saying_im_occupied_gets_the_call_later_flow():
    agent, call, store, session = make_agent()
    hear(agent, "haan ji")
    assert hear(agent, "I'm occupied, call me tomorrow") is True
    assert session.said[-1] == says(caller_words.TIME_FOR_DAY, day="Thursday 29 October")


# ---- never loop on the time ----

def test_call_later_asks_the_time_once():
    agent, call, store, session = make_agent()
    hear(agent, "haan, abhi busy hoon")
    hear(agent, "kal shaam")
    assert session.said[-1] == says(caller_words.TIME_FOR_DAY, day="Thursday 29 October")
    hear(agent, "shaam ko")  # still no hour: no second question
    assert session.closed is True
    assert _callbacks(store) == [("customer_requested", THURSDAY_11, "caller gave day only")]


def test_third_party_after_please_tell_the_time_a_day_books_at_once():
    agent, call, store, session = make_agent()
    hear(agent, "Sunita ghar pe nahi hai")
    hear(agent, "main unki behen hoon")
    hear(agent, "nahi")  # no consent: when can we reach Sunita?
    hear(agent, "shaam ko")
    assert session.said[-1] == caller_words.TIME_INCOMPLETE["hi"]
    hear(agent, "kal")  # the time was asked for once already
    assert session.said[-1] == says(caller_words.TP_CLOSE_DAY, day="Thursday 29 October")
    assert _callbacks(store) == [("customer_unavailable", THURSDAY_11, "caller gave day only")]


def test_hardship_without_a_date_never_asks_for_a_time():
    agent, call, store, session = make_agent()
    hear(agent, "haan ji")
    hear(agent, "meri job chali gayi")
    hear(agent, "abhi pata nahi")
    assert session.said[-1] == caller_words.HARDSHIP_NO_DATE_CLOSE["hi"] and session.closed
    assert not any(caller_words.TIME_FOR_DAY["hi"][:5] in s or "किस समय" in s for s in session.said)
    assert _callbacks(store) == [("human_followup", THURSDAY_11, "hardship, no pay date")]


# ---- no colleague for a busy customer ----

def test_the_guard_drops_colleague_sentences():
    g = ReplyGuard("hi", no_colleague=True)
    out = g.feed("ठीक है। हमारी एक सहयोगी आपको वापस कॉल करेंगी। ") + g.finish()
    assert "सहयोगी" not in out and ("colleague_in_call_later",
                                     "हमारी एक सहयोगी आपको वापस कॉल करेंगी।") in g.events
    g = ReplyGuard("en", no_colleague=True)
    assert "colleague" not in g.feed("Sure. A colleague will call you back. ") + g.finish()


@pytest.mark.parametrize("lines", [["I'm busy"], ["haan, abhi busy hoon"],
                                   ["haan, abhi busy hoon", "Monday 5 baje"]])
def test_the_agent_turns_the_colleague_rule_on(lines):
    agent, call, store, session = make_agent()
    assert agent._guard().no_colleague is False
    for text in lines:
        hear(agent, text)
    assert agent._guard().no_colleague is True


def test_hardship_still_mentions_the_colleague():
    agent, call, store, session = make_agent()
    hear(agent, "haan ji")
    hear(agent, "meri job chali gayi")
    assert agent._guard().no_colleague is False


@pytest.mark.parametrize("line", [caller_words.CALL_LATER_ASK, caller_words.CALL_LATER_CLOSE,
                                  caller_words.CALL_LATER_CLOSE_DAY,
                                  caller_words.CALL_LATER_CLOSE_NO_TIME, caller_words.TIME_FOR_DAY])
def test_call_later_lines_never_mention_a_colleague(line):
    assert not any(w in text for text in line.values() for w in COLLEAGUE_WORDS)
