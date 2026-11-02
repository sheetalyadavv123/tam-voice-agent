"""Batch 7, from a Console test: "मैं इसको दस दिन बाद pay कर दूंगी" got only "हमारी एक सहयोगी
आपको वापस कॉल करेंगी". Relative dates are resolved by the code; a date beyond
MAX_PROMISE_DAYS gets one try for an earlier one, then a colleague's callback with the
offered date in the note."""
import logging
from datetime import date, datetime

import pytest
from livekit.agents import llm

from tests.test_fixed_replies import (  # noqa: F401  (fake_session is an autouse fixture)
    FAR, FakeSession, fake_session, hear, main, run_tool,
)
from tools import caller_words, policy, relative_dates
from tools.collection_tools import Call
from tools.rules import IST
from tools.store import MemoryStore

MON = datetime(2026, 11, 2, 11, 0, tzinfo=IST)  # Monday 2 November: today in the Console test
WED = datetime(2026, 10, 28, 11, 0, tzinfo=IST)  # the other tests' NOW
FIRST = "मैं इसको दस दिन बाद pay कर दूंगी"
EARLIER_HI = "ठीक है, 12 November नोट किया। क्या आप 9 November तक pay कर पाएंगे?"


def make_agent(now=MON, customer="C002"):
    store = MemoryStore.from_files(clock=lambda: now)
    call = Call(store, customer, session_id="s1", clock=lambda: now)
    agent = main.Collector(call, "x")
    session = FakeSession()
    agent._session_for_test = session
    return agent, call, store, session


def verified(now=MON):
    agent, call, store, session = make_agent(now)
    assert hear(agent, "haan ji") is True  # the code gives the reminder
    return agent, call, store, session


def says(lines, **values):
    return caller_words.line(lines, "hi", name="Sunita", **values)


def callbacks(store):
    return [(c["reason"], c["due_at"], c["note"]) for c in store.callbacks_due(FAR)]


def promises(store):
    return [(p["promised_date"], p["status"]) for p in store.promises_for("C002")
            if p["session_id"] == "s1"]


# ---- every phrase, resolved by the code (IST) ----

@pytest.mark.parametrize("text,expected", [
    ("10 din baad", date(2026, 11, 12)),
    ("दस दिन बाद", date(2026, 11, 12)),
    (FIRST, date(2026, 11, 12)),
    ("ek hafte mein", date(2026, 11, 9)),
    ("agle hafte", date(2026, 11, 9)),
    ("अगले हफ्ते", date(2026, 11, 9)),
    ("2 hafte baad", date(2026, 11, 16)),
    ("do hafte baad", date(2026, 11, 16)),
    ("दो हफ्ते बाद", date(2026, 11, 16)),
    ("in 10 days", date(2026, 11, 12)),
    ("after ten days", date(2026, 11, 12)),
    ("10 days from now", date(2026, 11, 12)),
    ("next week", date(2026, 11, 9)),
    ("in a week", date(2026, 11, 9)),
    ("in two weeks", date(2026, 11, 16)),
    ("mahine ke end tak", date(2026, 11, 30)),
    ("महीने के आखिर तक", date(2026, 11, 30)),
    ("महीने के end तक", date(2026, 11, 30)),
    ("month end", date(2026, 11, 30)),
    ("end of the month", date(2026, 11, 30)),
    ("नहीं, 12 को ही", date(2026, 11, 12)),
    ("ठीक है, 9 तक कर दूंगी", date(2026, 11, 9)),
    ("15 tareekh ko", date(2026, 11, 15)),
    ("1 tareekh ko", date(2026, 12, 1)),  # the 1st has gone by: next month's
    ("by the 12th", date(2026, 11, 12)),
    ("aaj se 10 din baad", date(2026, 11, 12)),
    ("agle hafte Friday", date(2026, 11, 13)),  # the Friday of next week
    ("Friday tak", date(2026, 11, 6)),
])
def test_relative_dates_are_resolved(text, expected):
    assert relative_dates.last_pay_date(text, MON.date()) == expected


@pytest.mark.parametrize("text", ["5 baje tak", "₹500 तक", "abhi tak nahi", "do ko",
                                  "kuch din baad", "haan ji", "jab tak salary nahi aati"])
def test_not_a_pay_date(text):
    assert relative_dates.pay_dates(text, MON.date()) == []


def test_month_end_on_a_31_day_month():
    assert relative_dates.last_pay_date("month end", date(2026, 10, 28)) == date(2026, 10, 31)


def test_the_resolved_date_is_added_to_the_turn(caplog):
    agent, call, store, session = verified()
    ctx = llm.ChatContext()
    with caplog.at_level(logging.INFO, logger="tam-voice-agent"):
        agent._add_days_note(ctx, "agle hafte kar dungi")
    notes = [m.text_content for m in ctx.items if m.type == "message" and m.role == "system"]
    assert notes == ["The caller named these days (IST): agle hafte = Monday 9 November. "
                     "Use exactly these dates."]
    assert "RULE weekday_resolved: agle hafte = Monday 9 November" in caplog.text


# ---- too far: one try for earlier ----

def test_the_console_line_gets_the_earlier_question(caplog):
    agent, call, store, session = verified()
    with caplog.at_level(logging.INFO, logger="tam-voice-agent"):
        assert hear(agent, FIRST) is True  # the code, not the AI
    assert session.said[-1] == EARLIER_HI
    assert not any(w in session.said[-1] for w in ("सहयोगी", "colleague"))
    assert promises(store) == [] and callbacks(store) == []
    assert "RULE promise_date_far:" in caplog.text


def test_earlier_date_within_the_limit_records_the_promise(caplog):
    agent, call, store, session = verified()
    hear(agent, FIRST)
    with caplog.at_level(logging.INFO, logger="tam-voice-agent"):
        assert hear(agent, "ठीक है, 9 तक कर दूंगी") is False  # the AI confirms and offers the link
    assert promises(store) == [(date(2026, 11, 9), "open")]
    assert call.promise_recorded and callbacks(store) == [] and not call.handoff_requested
    assert "RULE promise_by_code" in caplog.text


def test_a_plain_yes_is_the_last_allowed_day():
    agent, call, store, session = verified()
    hear(agent, FIRST)
    assert hear(agent, "haan, kar dungi") is False
    assert promises(store) == [(date(2026, 11, 9), "open")]


def test_an_earlier_weekday_records_that_day():
    agent, call, store, session = verified()
    hear(agent, "2 hafte baad")
    assert session.said[-1] == says(caller_words.FAR_DATE_EARLIER, date="16 November",
                                    latest="9 November")
    hear(agent, "chalo Friday tak")
    assert promises(store) == [(date(2026, 11, 6), "open")]


def test_the_ai_gets_the_promise_note():
    agent, call, store, session = verified()
    hear(agent, FIRST)
    ctx = llm.ChatContext()
    line, hang_up = agent._far_reply(ctx, "ठीक है, 9 तक कर दूंगी")
    assert line is None
    note = ctx.items[-1].text_content
    assert "Monday 9 November" in note and "record_promise_to_pay" in note
    assert "offer the payment link once" in note


# ---- still too far: a colleague, the date in the note ----

def test_still_too_far_books_the_colleague_with_the_date(caplog):
    agent, call, store, session = verified()
    hear(agent, FIRST)
    with caplog.at_level(logging.INFO, logger="tam-voice-agent"):
        assert hear(agent, "नहीं, 12 को ही") is True
        assert session.said[-1] == says(caller_words.FAR_DATE_COLLEAGUE, date="12 November")
        assert session.said[-1].startswith("ठीक है, 12 November के लिए हमारी एक सहयोगी आपसे बात "
                                           "करके confirm करेंगी।")
        assert session.closed is False and callbacks(store) == []
        assert hear(agent, "kal shaam 5 baje") is True
    assert session.said[-1] == says(caller_words.FAR_DATE_CLOSE,
                                    when="Tuesday 3 November, शाम 5 बजे")
    assert session.closed is True
    note = "customer offered 12 November, beyond limit"
    assert callbacks(store) == [("human_followup", datetime(2026, 11, 3, 17, 0, tzinfo=IST), note)]
    assert call.outcome == "human_callback" and call.note == note
    assert call.handoff_requested and promises(store) == []
    assert "RULE promise_date_far_colleague" in caplog.text
    assert "RULE promise_date_far_handoff" in caplog.text


def test_the_time_is_asked_once_then_the_default_hour():
    agent, call, store, session = verified()
    hear(agent, FIRST)
    hear(agent, "nahi")
    hear(agent, "pata nahi")  # no time, no day: tomorrow at DEFAULT_CALLBACK_HOUR
    note = "customer offered 12 November, beyond limit; no time given, default hour"
    assert callbacks(store) == [("human_followup", datetime(2026, 11, 3, 11, 0, tzinfo=IST), note)]
    assert session.said[-1] == says(caller_words.FAR_DATE_CLOSE_DAY, day="Tuesday 3 November")
    assert call.outcome == "human_callback" and call.note == note and session.closed


def test_a_day_without_a_time_books_the_default_hour():
    agent, call, store, session = verified()
    hear(agent, FIRST)
    hear(agent, "nahi ho payega")
    hear(agent, "Wednesday")
    note = "customer offered 12 November, beyond limit; caller gave day only"
    assert callbacks(store) == [("human_followup", datetime(2026, 11, 4, 11, 0, tzinfo=IST), note)]
    assert session.said[-1] == says(caller_words.FAR_DATE_CLOSE_DAY, day="Wednesday 4 November")


def test_a_time_outside_the_hours_books_that_day_at_the_default_hour():
    agent, call, store, session = verified()
    hear(agent, FIRST)
    hear(agent, "nahi")
    hear(agent, "kal raat 10 baje")
    assert callbacks(store)[0][1] == datetime(2026, 11, 3, 11, 0, tzinfo=IST)
    assert session.closed


def test_a_later_far_date_replaces_the_offered_one():
    agent, call, store, session = verified()
    hear(agent, FIRST)
    hear(agent, "nahi, 15 tareekh ko")
    assert session.said[-1] == says(caller_words.FAR_DATE_COLLEAGUE, date="15 November")
    hear(agent, "kal 4 baje")
    assert callbacks(store)[0][2] == "customer offered 15 November, beyond limit"


@pytest.mark.parametrize("answer", ["9 तक नहीं हो पाएगा", "nahi", "that's not possible",
                                    "dekhti hoon", "मुश्किल है"])
def test_anything_but_an_earlier_date_goes_to_the_colleague(answer):
    agent, call, store, session = verified()
    hear(agent, FIRST)
    hear(agent, answer)
    assert session.said[-1] == says(caller_words.FAR_DATE_COLLEAGUE, date="12 November")
    assert promises(store) == []


def test_english():
    agent, call, store, session = make_agent(customer="C003")  # Amit, English
    assert hear(agent, "yes") is True
    assert hear(agent, "I can pay in 10 days") is True
    assert session.said[-1] == ("All right, I've noted 12 November. "
                                "Would you be able to pay by 9 November?")
    hear(agent, "no")
    assert session.said[-1] == ("All right. A colleague will talk to you to confirm 12 November. "
                                "What day and time would suit you for their call?")
    hear(agent, "tomorrow at 4 pm")
    assert session.said[-1] == ("All right. Our colleague will call you on Tuesday 3 November "
                                "at 4 pm. Thank you, have a good day.")


# ---- the limit is a setting ----

def test_max_promise_days_is_the_limit(monkeypatch):
    monkeypatch.setattr(policy, "MAX_PROMISE_DAYS", 10)
    agent, call, store, session = verified()
    assert hear(agent, FIRST) is False  # 12 November is within 10 days: the AI records it
    assert agent.far.state is None


def test_within_seven_days_is_left_to_the_ai():
    agent, call, store, session = verified()
    assert hear(agent, "ek hafte mein kar dungi") is False  # 9 November: the last allowed day
    assert agent.far.state is None


def test_the_ais_too_far_promise_goes_to_the_code(caplog):
    agent, call, store, session = verified()
    call.heard("12 November ko")
    assert run_tool(agent, agent.record_promise_to_pay(None, 12, 11, 2026)) == "stopped"
    assert session.said[-1] == EARLIER_HI and agent.far.state == "asked"


def test_the_earlier_question_is_asked_once():
    agent, call, store, session = verified()
    hear(agent, FIRST)
    hear(agent, "nahi")  # colleague
    assert session.said.count(EARLIER_HI) == 1


# ---- not a far pay date ----

def test_call_me_in_ten_days_is_not_a_pay_date():
    agent, call, store, session = verified()
    hear(agent, "10 din baad call karna")
    assert agent.far.state is None


def test_hardship_with_a_far_date_also_gets_the_earlier_question():
    agent, call, store, session = verified()
    assert hear(agent, "meri job chali gayi, 10 din baad de dungi") is True
    assert session.said[-1] == EARLIER_HI and call.hardship == "with_date"


def test_before_identity_nothing_happens():
    agent, call, store, session = make_agent()
    hear(agent, "kaun? 10 din baad")
    assert agent.far.state is None and not call.unlocked


def test_from_the_other_tests_wednesday():
    # from Wed 28 October the last allowed day is Wed 4 November
    agent, call, store, session = verified(WED)
    hear(agent, "agle hafte Friday")
    assert session.said[-1] == says(caller_words.FAR_DATE_EARLIER, date="6 November",
                                    latest="4 November")


# ---- someone else, verified ----

def test_a_verified_relative_with_a_far_date():
    agent, call, store, session = make_agent()
    for text in ["Sunita ghar pe nahi hai", "main unki behen hoon", "haan bataiye", "2 November 1985"]:
        hear(agent, text)
    assert call.unlocked and call.person_status == "third_party"
    assert hear(agent, "wo das din baad kar degi") is True
    assert session.said[-1] == says(caller_words.FAR_DATE_EARLIER_TP, date="12 November",
                                    latest="9 November")
    hear(agent, "nahi")
    assert session.said[-1] == says(caller_words.FAR_DATE_COLLEAGUE_TP, date="12 November")
    hear(agent, "kal 4 baje")
    assert session.said[-1] == says(caller_words.FAR_DATE_CLOSE_TP,
                                    when="Tuesday 3 November, शाम 4 बजे")
    assert callbacks(store)[0][0] == "human_followup"
    assert callbacks(store)[0][2].endswith("offered 12 November, beyond limit")
    assert call.outcome == "human_callback"


@pytest.mark.parametrize("lines", [caller_words.FAR_DATE_EARLIER, caller_words.FAR_DATE_EARLIER_TP])
def test_the_earlier_question_never_mentions_a_colleague(lines):
    assert not any(w in t for t in lines.values() for w in ("सहयोगी", "colleague"))
