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
                     "Use exactly these dates; for a range, the promise date is its last day."]
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

TUESDAY_11 = datetime(2026, 11, 3, 11, 0, tzinfo=IST)  # the next default-hour slot from MON 11:00
CLOSE_12_HI = ("ठीक है, 12 November थोड़ा आगे की तारीख है। इसके बारे में हमारी टीम से कोई आपसे बात "
               "करेगा। धन्यवाद, आपका दिन शुभ हो।")
NOTE_12 = "customer offered 12 November, beyond limit"


def test_still_too_far_ends_the_call_with_the_fixed_line(caplog):
    agent, call, store, session = verified()
    hear(agent, FIRST)
    with caplog.at_level(logging.INFO, logger="tam-voice-agent"):
        assert hear(agent, "नहीं, 12 को ही") is True
    assert session.said[-1] == CLOSE_12_HI
    assert session.closed is True  # no question about a day or time
    assert callbacks(store) == [("human_followup", TUESDAY_11, NOTE_12)]
    assert call.outcome == "human_callback" and call.note == NOTE_12
    assert call.handoff_requested and promises(store) == []
    assert "RULE promise_date_far_close" in caplog.text
    assert "RULE promise_date_far_handoff" in caplog.text


def test_the_close_never_asks_for_a_day_or_time():
    for text in caller_words.FAR_DATE_CLOSE.values():
        assert "?" not in text and "समय" not in text and "time" not in text


def test_the_slot_is_today_when_the_default_hour_is_still_ahead():
    early = datetime(2026, 11, 2, 9, 30, tzinfo=IST)  # 9:30, before 11
    agent, call, store, session = verified(early)
    hear(agent, FIRST)
    hear(agent, "nahi")
    assert callbacks(store) == [("human_followup", datetime(2026, 11, 2, 11, 0, tzinfo=IST), NOTE_12)]


def test_after_the_calling_hours_the_slot_is_tomorrow():
    late = datetime(2026, 11, 2, 18, 30, tzinfo=IST)
    agent, call, store, session = verified(late)
    hear(agent, FIRST)
    hear(agent, "nahi")
    assert callbacks(store)[0][1] == TUESDAY_11


def test_a_later_far_date_is_the_one_in_the_note():
    agent, call, store, session = verified()
    hear(agent, FIRST)
    hear(agent, "नहीं, 13 को")
    assert session.said[-1] == says(caller_words.FAR_DATE_CLOSE, date="13 November")
    assert callbacks(store) == [("human_followup", TUESDAY_11,
                                 "customer offered 13 November, beyond limit")]


@pytest.mark.parametrize("answer", ["9 तक नहीं हो पाएगा", "nahi", "no", "that's not possible",
                                    "मुश्किल है", "नहीं", "12 को ही"])
def test_a_no_or_a_far_date_closes_at_once(answer):
    agent, call, store, session = verified()
    hear(agent, FIRST)
    assert hear(agent, answer) is True
    assert session.said[-1].startswith("ठीक है, 12 November थोड़ा आगे की तारीख है।")
    assert session.closed and promises(store) == []
    assert session.said.count(EARLIER_HI) == 1


# ---- an unclear answer is not a no ----

@pytest.mark.parametrize("answer", ["నైన్", "ఆండీ", "", "dekhti hoon", "hmm"])
def test_an_unclear_answer_gets_the_same_question_once_more(answer, caplog):
    agent, call, store, session = verified()
    hear(agent, FIRST)
    with caplog.at_level(logging.INFO, logger="tam-voice-agent"):
        assert hear(agent, answer) is True
    assert session.said[-2:] == [EARLIER_HI, EARLIER_HI]
    assert session.closed is False and callbacks(store) == [] and call.outcome is None
    assert "RULE promise_date_far_unclear" in caplog.text


@pytest.mark.parametrize("answer", ["నైన్", "ఆండీ", ""])
def test_two_unclear_answers_close(answer):
    agent, call, store, session = verified()
    hear(agent, FIRST)
    hear(agent, answer)
    hear(agent, answer)
    assert session.said.count(EARLIER_HI) == 2
    assert session.said[-1] == CLOSE_12_HI and session.closed
    assert callbacks(store) == [("human_followup", TUESDAY_11, NOTE_12)]


def test_unclear_then_an_earlier_date_records_the_promise():
    agent, call, store, session = verified()
    hear(agent, FIRST)
    hear(agent, "ఆండీ")
    assert hear(agent, "9 तक") is False
    assert promises(store) == [(date(2026, 11, 9), "open")] and callbacks(store) == []


def test_unclear_then_no_closes():
    agent, call, store, session = verified()
    hear(agent, FIRST)
    hear(agent, "నైన్")
    hear(agent, "nahi")
    assert session.said[-1] == CLOSE_12_HI


def test_english():
    agent, call, store, session = make_agent(customer="C003")  # Amit, English
    assert hear(agent, "yes") is True
    assert hear(agent, "I can pay in 10 days") is True
    assert session.said[-1] == ("All right, I've noted 12 November. "
                                "Would you be able to pay by 9 November?")
    hear(agent, "no")
    assert session.said[-1] == ("All right, 12 November is a bit further out than I can note. "
                                "Someone from our team will talk to you about it. Thank you, "
                                "have a good day.")
    assert session.closed and callbacks(store)[0][0] == "human_followup"


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
    hear(agent, "nahi")  # the close
    assert session.said.count(EARLIER_HI) == 1


# ---- not a far pay date ----

def test_call_me_in_ten_days_is_not_a_pay_date():
    agent, call, store, session = verified()
    hear(agent, "10 din baad call karna")
    assert agent.far.state is None


# ---- ranges ----

@pytest.mark.parametrize("text,first,last", [
    ("10-15 din", date(2026, 11, 12), date(2026, 11, 17)),
    ("10-15 din mein", date(2026, 11, 12), date(2026, 11, 17)),
    ("10 se 15 din mein", date(2026, 11, 12), date(2026, 11, 17)),
    ("10 to 15 days", date(2026, 11, 12), date(2026, 11, 17)),
    ("in 10 to 15 days", date(2026, 11, 12), date(2026, 11, 17)),
    ("दस से पंद्रह दिन में", date(2026, 11, 12), date(2026, 11, 17)),
    ("2-3 hafte", date(2026, 11, 16), date(2026, 11, 23)),
    ("next month", date(2026, 12, 1), date(2026, 12, 31)),
    ("agle mahine tak", date(2026, 12, 1), date(2026, 12, 31)),
    ("अगले महीने", date(2026, 12, 1), date(2026, 12, 31)),
])
def test_ranges_keep_both_days(text, first, last):
    o = relative_dates.last_offer(text, MON.date())
    assert (o.first, o.last) == (first, last)
    assert relative_dates.last_pay_date(text, MON.date()) == last  # the limit uses the later day


def test_a_range_note_has_both_days():
    assert relative_dates.note_for("10-15 din mein", MON.date()) == (
        "10-15 din mein = Thursday 12 November to Tuesday 17 November")


def test_say_a_range():
    o = relative_dates.last_offer("10-15 din", MON.date())
    assert relative_dates.say_offer(o, "hi") == "12 से 17 November"
    assert relative_dates.say_offer(o) == "12 to 17 November"
    nxt = relative_dates.last_offer("next month", MON.date())
    assert relative_dates.say_offer(nxt, "hi") == "अगले महीने"  # as the caller said it
    assert relative_dates.say_offer(nxt) == "next month"
    assert relative_dates.say_offer(nxt, for_note=True) == "next month (December)"
    agle = relative_dates.last_offer("agle mahine tak", MON.date())
    assert relative_dates.say_offer(agle, "hi") == "अगले महीने"


@pytest.mark.parametrize("text,days", [("15 din lagenge", 15), ("10 din lagega", 10),
                                       ("दस दिन लगेंगे", 10), ("2 hafte lagenge", 14)])
def test_it_will_take_n_days(text, days):
    from datetime import timedelta
    assert relative_dates.last_pay_date(text, MON.date()) == MON.date() + timedelta(days=days)


def test_a_range_ending_within_the_limit_is_not_too_far():
    agent, call, store, session = verified()
    assert hear(agent, "3-5 din mein kar dungi") is False  # 5 to 7 November: the AI records it
    assert agent.far.state is None


def test_a_range_ending_beyond_the_limit_gets_the_earlier_question():
    agent, call, store, session = verified()
    hear(agent, "5 se 10 din mein")  # ends 12 November
    assert session.said[-1] == says(caller_words.FAR_DATE_EARLIER, date="7 से 12 November",
                                    latest="9 November")
    hear(agent, "nahi")
    assert callbacks(store)[0][2] == "customer offered 7 to 12 November, beyond limit"


# ---- hardship and a date ----

HARDSHIP_FAR_HI = ("मैं समझ सकती हूँ, Sunita जी। आपने जो {date} बताया है, उसके बारे में हमारी टीम से "
                   "कोई आपसे बात करेगा। अपना ध्यान रखिए, धन्यवाद।")


@pytest.mark.parametrize("text,said,note", [
    ("job chali gayi, 10-15 din mein kar dungi", "12 से 17 November", "12 to 17 November"),
    ("salary nahi aayi, next month", "अगले महीने", "next month (December)"),
    ("meri job chali gayi, 10 din baad de dungi", "12 November", "12 November"),
])
def test_hardship_beyond_the_limit_ends_kindly_with_no_earlier_question(text, said, note, caplog):
    agent, call, store, session = verified()
    with caplog.at_level(logging.INFO, logger="tam-voice-agent"):
        assert hear(agent, text) is True
    assert session.said[-1] == HARDSHIP_FAR_HI.format(date=said)
    assert EARLIER_HI not in session.said and "pay कर पाएंगे" not in session.said[-1]
    assert session.closed is True  # no time question
    full = f"hardship, offered {note}, beyond limit"
    assert callbacks(store) == [("human_followup", TUESDAY_11, full)]
    assert call.outcome == "hardship" and call.note == full
    assert call.hardship == "far_date" and promises(store) == []
    assert "RULE hardship_far_date" in caplog.text


def test_hardship_within_the_limit_is_unchanged():
    agent, call, store, session = verified()
    assert hear(agent, "paise nahi hain, 5 din mein kar dungi") is False  # the AI saves the promise
    assert call.hardship == "with_date" and callbacks(store) == [] and agent.far.state is None
    assert agent._guard().no_callback_talk is True  # no colleague: the founder's rule


def test_the_no_colleague_rule_is_only_for_a_date_within_the_limit():
    agent, call, store, session = verified()
    hear(agent, "job chali gayi, 10-15 din mein kar dungi")
    assert agent._guard().no_callback_talk is False


def test_hardship_then_a_far_date_after_the_date_question():
    agent, call, store, session = verified()
    hear(agent, "salary nahi aayi abhi")  # the code asks once for a date
    assert session.said[-1] == says(caller_words.HARDSHIP_DATE_ASK)
    hear(agent, "15 din mein")
    assert session.said[-1] == HARDSHIP_FAR_HI.format(date="17 November") and session.closed
    assert call.outcome == "hardship"


def test_hardship_at_the_greeting_with_a_far_date():
    agent, call, store, session = make_agent()
    assert hear(agent, "haan ji, job chali gayi, next month kar dungi") is True
    assert session.said[-1] == HARDSHIP_FAR_HI.format(date="अगले महीने") and session.closed


def test_hardship_far_in_english():
    agent, call, store, session = make_agent(customer="C003")
    hear(agent, "yes")
    hear(agent, "I lost my job, I can pay in 10 to 15 days")
    assert session.said[-1] == ("I understand, Amit. Someone from our team will talk to you "
                                "about 12 to 17 November, as you mentioned. Take care, thank you.")


def test_the_ais_too_far_promise_after_hardship_gets_the_kind_close():
    agent, call, store, session = verified()
    hear(agent, "paise nahi hain, 5 din mein kar dungi")  # within: with_date, the AI's turn
    assert run_tool(agent, agent.record_promise_to_pay(None, 20, 11, 2026)) == "stopped"
    assert session.said[-1] == HARDSHIP_FAR_HI.format(date="20 November") and session.closed


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
    assert session.said[-1] == says(caller_words.FAR_DATE_CLOSE_TP, date="12 November")
    assert "Sunita जी से बात करेगा" in session.said[-1] and session.closed
    assert callbacks(store)[0][:2] == ("human_followup", TUESDAY_11)
    assert callbacks(store)[0][2].endswith("offered 12 November, beyond limit")
    assert call.outcome == "human_callback"


@pytest.mark.parametrize("lines", [caller_words.FAR_DATE_EARLIER, caller_words.FAR_DATE_EARLIER_TP])
def test_the_earlier_question_never_mentions_a_colleague(lines):
    assert not any(w in t for t in lines.values() for w in ("सहयोगी", "colleague"))
