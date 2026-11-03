"""Batch 7: the order of the rules (tools/precedence), enforced in one place:
legal -> wrong number -> hardship beyond the limit -> plain beyond the limit ->
hardship within the limit -> normal promise; and hardship with no date."""
import logging
from datetime import date, datetime

import pytest

from tests.test_far_dates import (  # noqa: F401  (fake_session is an autouse fixture)
    EARLIER_HI, FIRST, MON, TUESDAY_11, callbacks, fake_session, hear, make_agent, promises,
    run_tool, says, verified,
)
from tools import caller_words, precedence
from tools.relative_dates import Offer, single
from tools.rules import IST

LATEST = date(2026, 11, 9)  # MON + 7
FAR = single(date(2026, 11, 12))
NEAR = single(date(2026, 11, 6))
NO_DATE_CLOSE_HI = "मैं समझ सकती हूँ। इसके बारे में हमारी टीम से कोई आपसे बात करेगा। अपना ध्यान रखिए, धन्यवाद।"


def hardship_far_hi(d, name="Sunita"):
    return (f"मैं समझ सकती हूँ, {name} जी। आपने जो {d} बताया है, उसके बारे में हमारी टीम से कोई आपसे "
            "बात करेगा। अपना ध्यान रखिए, धन्यवाद।")


# ---- the order, as a table ----

@pytest.mark.parametrize("text,offer,before,expected", [
    ("case hoga kya? job chali gayi", FAR, False, precedence.LEGAL),  # legal beats all
    ("legal notice bhejoge? 10 din baad", FAR, True, precedence.LEGAL),
    ("wrong number hai, job chali gayi", FAR, False, precedence.WRONG_NUMBER),
    ("galat number", NEAR, True, precedence.WRONG_NUMBER),
    ("job chali gayi", FAR, False, precedence.HARDSHIP_FAR),  # beats plain far
    ("kar dungi", FAR, True, precedence.HARDSHIP_FAR),  # hardship told earlier
    ("kar dungi", FAR, False, precedence.FAR),
    ("job chali gayi", NEAR, False, precedence.HARDSHIP_WITHIN),
    ("kar dungi", NEAR, True, precedence.HARDSHIP_WITHIN),
    ("kar dungi", NEAR, False, precedence.PROMISE),
    ("job chali gayi", None, False, precedence.HARDSHIP_NO_DATE),
    ("theek hai", None, False, None),
])
def test_the_order(text, offer, before, expected):
    assert precedence.route(text, "Sunita", offer=offer, latest=LATEST,
                            hardship_before=before) == expected


def test_a_range_counts_by_its_last_day():
    straddles = Offer("5-10 din", date(2026, 11, 7), date(2026, 11, 12))
    assert precedence.route("kar dungi", "Sunita", offer=straddles, latest=LATEST) == precedence.FAR


def test_after_the_push_stopped_only_legal_and_wrong_number():
    kw = dict(offer=FAR, latest=LATEST, push_stopped=True)
    assert precedence.route("job chali gayi", "Sunita", **kw) is None
    assert precedence.route("case hoga?", "Sunita", **kw) == precedence.LEGAL
    assert precedence.route("wrong number", "Sunita", **kw) == precedence.WRONG_NUMBER


def test_the_order_is_listed_once():
    assert precedence.ORDER == (precedence.LEGAL, precedence.WRONG_NUMBER, precedence.HARDSHIP_FAR,
                                precedence.FAR, precedence.HARDSHIP_WITHIN, precedence.PROMISE,
                                precedence.HARDSHIP_NO_DATE)


# ---- inside the too-far flow ----

def test_legal_question_while_asking_for_an_earlier_date(caplog):
    agent, call, store, session = verified()
    hear(agent, FIRST)
    with caplog.at_level(logging.INFO, logger="tam-voice-agent"):
        assert hear(agent, "court case karoge kya?") is True
    assert session.said[-1] == caller_words.LEGAL_LINE["hi"] and session.closed
    assert call.outcome == "human_callback" and agent.far.state == "done"
    assert "RULE path: legal" in caplog.text


def test_wrong_number_while_asking_for_an_earlier_date():
    agent, call, store, session = verified()
    hear(agent, FIRST)
    assert hear(agent, "yeh wrong number hai") is True
    assert session.said[-1] == caller_words.WRONG_NUMBER_CLOSE["hi"] and session.closed
    assert call.outcome == "wrong_number" and callbacks(store) == []


def test_hardship_without_a_date_in_the_flow_switches_to_the_hardship_close():
    agent, call, store, session = verified()
    hear(agent, FIRST)
    assert hear(agent, "nahi, meri job chali gayi hai") is True
    assert session.said[-1] == hardship_far_hi("12 November") and session.closed
    assert callbacks(store) == [("human_followup", TUESDAY_11,
                                 "hardship, offered 12 November, beyond limit")]
    assert call.outcome == "hardship"


def test_hardship_with_another_far_date_in_the_flow():
    agent, call, store, session = verified()
    hear(agent, FIRST)
    hear(agent, "salary nahi aayi, 15 tareekh ko")
    assert session.said[-1] == hardship_far_hi("15 November")
    assert callbacks(store)[0][2] == "hardship, offered 15 November, beyond limit"


def test_hardship_said_after_one_unclear_answer():
    agent, call, store, session = verified()
    hear(agent, FIRST)
    hear(agent, "ఆండీ")  # the same question once more
    hear(agent, "ghar mein problem chal rahi hai")
    assert session.said[-1] == hardship_far_hi("12 November") and call.outcome == "hardship"


def test_hardship_with_an_earlier_date_in_the_flow_saves_the_promise():
    agent, call, store, session = verified()
    hear(agent, FIRST)
    assert hear(agent, "paise ki dikkat hai, par 9 tak kar dungi") is False
    assert promises(store) == [(date(2026, 11, 9), "open")]
    assert call.hardship == "with_date" and callbacks(store) == []


# ---- a later far date after a hardship promise within the limit ----

def test_a_later_far_date_withdraws_the_promise_and_lets_the_followup_be_spoken(caplog):
    agent, call, store, session = verified()
    assert hear(agent, "paise nahi hain, 5 din mein kar dungi") is False  # within: the AI's turn
    assert call.record_promise_to_pay(7, 11, 2026)["saved"]  # what the AI does
    assert agent._guard().no_callback_talk is True  # no colleague while the date is within
    with caplog.at_level(logging.INFO, logger="tam-voice-agent"):
        assert hear(agent, "actually 20 tareekh tak hi ho payega") is True
    assert session.said[-1] == hardship_far_hi("20 November") and session.closed
    assert call.hardship == "far_date" and agent._guard().no_callback_talk is False
    assert promises(store) == [(date(2026, 11, 7), "cancelled")]
    assert not call.promise_recorded and call.outcome == "hardship"
    assert callbacks(store) == [("human_followup", TUESDAY_11,
                                 "hardship, offered 20 November, beyond limit")]
    assert "RULE promise_withdrawn" in caplog.text


def test_a_plain_promise_then_a_far_date_is_left_to_the_ai():
    # no hardship: changing a saved promise stays with the AI and record_promise_to_pay
    agent, call, store, session = verified()
    call.record_promise_to_pay(6, 11, 2026)
    assert hear(agent, "nahi, 20 tareekh ko") is False and agent.far.state is None


# ---- hardship without any date ----

def test_hardship_without_a_date_asks_once_then_closes(caplog):
    agent, call, store, session = verified()
    with caplog.at_level(logging.INFO, logger="tam-voice-agent"):
        assert hear(agent, "meri job chali gayi") is True
        assert session.said[-1] == says(caller_words.HARDSHIP_DATE_ASK)
        assert hear(agent, "abhi kuch nahi keh sakti") is True
    assert session.said[-1] == NO_DATE_CLOSE_HI and session.closed
    assert "?" not in session.said[-1]  # no day or time question
    assert callbacks(store) == [("human_followup", TUESDAY_11, "hardship, no pay date")]
    assert call.outcome == "hardship" and call.note == "hardship, no pay date"
    assert "RULE hardship_date_ask" in caplog.text and "RULE hardship_no_date" in caplog.text


def test_hardship_without_a_date_then_a_date_within():
    agent, call, store, session = verified()
    hear(agent, "meri job chali gayi")
    assert hear(agent, "Friday tak") is False
    assert call.hardship == "with_date" and callbacks(store) == []


def test_hardship_without_a_date_then_a_far_date():
    agent, call, store, session = verified()
    hear(agent, "meri job chali gayi")
    hear(agent, "2-3 hafte lagenge")
    assert session.said[-1] == hardship_far_hi("16 से 23 November")


def test_hardship_without_a_date_in_english():
    agent, call, store, session = make_agent(customer="C003")
    hear(agent, "yes")
    hear(agent, "things are tough right now")
    assert session.said[-1] == ("I understand, Amit. Could you tell me a date by which you'll be "
                                "able to pay?")
    hear(agent, "I don't know")
    assert session.said[-1] == ("I understand. Someone from our team will talk to you about it. "
                                "Take care, thank you.")


# ---- indirect hardship ----

@pytest.mark.parametrize("text", ["ghar mein problem chal rahi hai", "abhi haalat theek nahi hai",
                                  "things are tough right now", "घर में परेशानी चल रही है",
                                  "हालत ठीक नहीं है", "going through a tough time"])
def test_indirect_hardship_is_hardship(text):
    assert caller_words.hardship(text)
    agent, call, store, session = verified()
    assert hear(agent, text) is True
    assert session.said[-1] == says(caller_words.HARDSHIP_DATE_ASK) and call.hardship == "asked_date"


@pytest.mark.parametrize("text", ["ghar pe hoon", "haalat theek hai", "things are fine",
                                  "I'm having a hard time hearing you"])
def test_not_hardship(text):
    assert not caller_words.hardship(text)


# ---- a verified relative, in the third person ----

def relative():
    agent, call, store, session = make_agent()
    for text in ["Sunita ghar pe nahi hai", "main unki behen hoon", "haan bataiye", "2 November 1985"]:
        hear(agent, text)
    assert call.unlocked and call.person_status == "third_party"
    return agent, call, store, session


def test_relative_hardship_without_a_date():
    agent, call, store, session = relative()
    assert hear(agent, "unki job chali gayi hai") is True
    assert session.said[-1] == ("मैं समझ सकती हूँ। क्या आप बता पाएंगे कि Sunita जी किस तारीख तक pay "
                                "कर पाएंगे?")
    hear(agent, "pata nahi")
    assert session.said[-1] == ("मैं समझ सकती हूँ। इसके बारे में हमारी टीम से कोई Sunita जी से बात "
                                "करेगा। अपना ध्यान रखिए, धन्यवाद।")
    assert session.closed and call.outcome == "hardship"
    assert callbacks(store)[0][:2] == ("human_followup", TUESDAY_11)


def test_relative_hardship_beyond_the_limit():
    agent, call, store, session = relative()
    hear(agent, "unki tabiyat kharab hai, hospital mein hain, 10-15 din lagenge")
    assert session.said[-1] == ("मैं समझ सकती हूँ। आपने जो 12 से 17 November बताया है, उसके बारे में "
                                "हमारी टीम से कोई Sunita जी से बात करेगा। अपना ध्यान रखिए, धन्यवाद।")
    assert session.closed and call.outcome == "hardship"
    assert callbacks(store)[0][2] == "hardship, offered 12 to 17 November, beyond limit"


def test_relative_hardship_within_the_limit():
    agent, call, store, session = relative()
    assert hear(agent, "unki salary nahi aayi, Friday tak kar degi") is False
    assert call.hardship == "with_date" and callbacks(store) == []
    assert agent._guard().no_callback_talk is True


# ---- the AI's too-far promise goes through the same order ----

def test_the_ais_too_far_promise_after_hardship_within_withdraws_it():
    agent, call, store, session = verified()
    hear(agent, "paise nahi hain, 5 din mein kar dungi")
    call.record_promise_to_pay(7, 11, 2026)
    assert run_tool(agent, agent.record_promise_to_pay(None, 20, 11, 2026)) == "stopped"
    assert session.said[-1] == hardship_far_hi("20 November") and session.closed
    assert promises(store) == [(date(2026, 11, 7), "cancelled")]


def test_hardship_at_the_greeting_without_a_date():
    agent, call, store, session = make_agent()
    assert hear(agent, "haan ji, abhi haalat theek nahi hai") is True
    assert session.said == [says(caller_words.HARDSHIP_DATE_ASK)] and call.unlocked


def test_the_close_lines_never_ask_a_question():
    for lines in (caller_words.HARDSHIP_NO_DATE_CLOSE, caller_words.HARDSHIP_NO_DATE_CLOSE_TP,
                  caller_words.HARDSHIP_FAR_CLOSE, caller_words.HARDSHIP_FAR_CLOSE_TP):
        assert not any("?" in t for t in lines.values())


def test_next_default_slot_before_eleven_is_today():
    early = datetime(2026, 11, 2, 9, 0, tzinfo=IST)
    agent, call, store, session = verified(early)
    hear(agent, "meri job chali gayi")
    hear(agent, "pata nahi")
    assert callbacks(store)[0][1] == datetime(2026, 11, 2, 11, 0, tzinfo=IST)
