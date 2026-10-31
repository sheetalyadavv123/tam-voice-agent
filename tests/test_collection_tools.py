from datetime import date, datetime

import pytest

from tests.helpers import confirmed
from tools import policy
from tools.collection_tools import Call
from tools.rules import IST
from tools.store import MemoryStore

NOW = datetime(2026, 10, 28, 11, 0, tzinfo=IST)
FAR = datetime(2100, 1, 1, tzinfo=IST)


@pytest.fixture(autouse=True)
def default_mode(monkeypatch):
    monkeypatch.setattr(policy, "VERIFICATION_MODE", "name_only")


@pytest.fixture
def store():
    return MemoryStore.from_files(clock=lambda: NOW)


def make_call(store, customer_id="C002"):
    return Call(store, customer_id, session_id="s1", clock=lambda: NOW)


def test_first_name_and_language(store):
    assert make_call(store, "C007").first_name == "कविता"
    assert make_call(store, "C003").language == "en"
    assert make_call(store, "C002").language == "hi"


def test_unknown_customer_rejected(store):
    with pytest.raises(ValueError):
        make_call(store, "C999")


def test_nothing_before_person_reported(store):
    assert make_call(store).get_due_details() == {"error": "not_unlocked"}


def test_invalid_person_status(store):
    assert make_call(store).report_person("robot")["ok"] is False


def test_third_party_gets_nothing(store):
    c = make_call(store)
    assert c.report_person("third_party")["next"] == "share_nothing_and_end_politely"
    assert c.get_due_details() == {"error": "not_unlocked"}
    c.heard("actually main hi Sunita hoon")
    assert confirmed(c)["next"] == "ask_date_of_birth"
    assert c.get_due_details() == {"error": "not_unlocked"}


def test_name_only_unlocks_reminder(store):
    c = make_call(store, "C002")
    assert confirmed(c)["next"] == "give_reminder"
    d = c.get_due_details()
    assert d["emi_amount"] == 2200 and d["overdue_days"] == 44 and d["due_date"] == "2026-09-10"
    assert "outstanding" not in d and "dob" not in d


def test_name_only_verify_not_required(store):
    c = make_call(store)
    confirmed(c)
    assert c.verify_identity(2, 11, 1985) == {"verified": True, "not_required": True}


def test_verify_needs_person_first(store):
    assert make_call(store).verify_identity(2, 11, 1985) == {"error": "confirm_person_first"}


def test_dob_mode_requires_dob(store, monkeypatch):
    monkeypatch.setattr(policy, "VERIFICATION_MODE", "name_plus_dob")
    c = make_call(store, "C002")
    assert confirmed(c)["next"] == "ask_date_of_birth"
    c.heard("2 November 1985")
    assert c.get_due_details() == {"error": "not_unlocked"}
    assert c.verify_identity(1, 1, 2000) == {"verified": False, "attempts_left": 2}
    assert c.verify_identity(2, 11, 1985)["verified"] is True
    assert c.get_due_details()["emi_amount"] == 2200


def test_dob_lockout(store, monkeypatch):
    monkeypatch.setattr(policy, "VERIFICATION_MODE", "name_plus_dob")
    c = make_call(store, "C002")
    confirmed(c)
    c.heard("2 November 1985")
    c.verify_identity(1, 1, 2000)
    c.verify_identity(2, 2, 2000)
    assert c.verify_identity(3, 3, 2000)["locked"] is True
    assert c.handoff_requested is True
    assert c.verify_identity(2, 11, 1985)["locked"] is True
    assert c.get_due_details() == {"error": "not_unlocked"}


def test_impossible_dates_do_not_use_attempts(store, monkeypatch):
    monkeypatch.setattr(policy, "VERIFICATION_MODE", "name_plus_dob")
    c = make_call(store, "C002")
    confirmed(c)
    c.heard("2 November 1985")
    assert c.verify_identity(31, 2, 1990)["error"] == "impossible_date"
    assert c.verify_identity(31, 2, 1990)["error"] == "impossible_date"
    assert c.verify_identity(1, 1, 2000) == {"verified": False, "attempts_left": 2}


def test_impossible_dates_cap_hands_over(store, monkeypatch):
    monkeypatch.setattr(policy, "VERIFICATION_MODE", "name_plus_dob")
    c = make_call(store, "C002")
    confirmed(c)
    c.heard("2 November 1985")
    c.verify_identity(31, 2, 1990)
    c.verify_identity(31, 2, 1990)
    assert c.verify_identity(31, 2, 1990)["locked"] is True
    assert c.handoff_requested is True and c.dob_attempts == 0


def test_two_digit_year_rejected(store, monkeypatch):
    monkeypatch.setattr(policy, "VERIFICATION_MODE", "name_plus_dob")
    c = make_call(store, "C002")
    confirmed(c)
    c.heard("2 November 1985")
    assert c.verify_identity(2, 11, 85)["error"] == "impossible_date"


def test_closed_loan(store):
    c = make_call(store, "C009")
    confirmed(c)
    d = c.get_due_details()
    assert d["loan_closed"] is True and "emi_amount" not in d


def test_open_promise_shown(store):
    c = make_call(store, "C011")
    confirmed(c)
    assert c.get_due_details()["open_promise"] == {"date": "2026-10-27", "amount": 5400}

# ---- rules moved from the prompt into code ----

def test_reminder_facts_come_with_report_person(store):
    c = make_call(store, "C002")
    r = confirmed(c)
    assert r["next"] == "give_reminder"
    assert r["due"]["emi_amount"] == 2200 and r["due"]["due_date"] == "2026-09-10"


def test_reminder_facts_come_with_verified_dob(store, monkeypatch):
    monkeypatch.setattr(policy, "VERIFICATION_MODE", "name_plus_dob")
    c = make_call(store, "C002")
    assert "due" not in confirmed(c)
    c.heard("2 November 1985")
    c.heard("2 November 1985")
    r = c.verify_identity(2, 11, 1985)
    assert r["verified"] is True and r["due"]["emi_amount"] == 2200


def test_repeated_customer_report_is_harmless(store):
    c = make_call(store, "C002")
    confirmed(c)
    r = confirmed(c)
    assert r == {"ok": True, "duplicate": True, "next": "continue_the_conversation"}
    assert c.unlocked


def test_repeated_customer_report_still_asks_dob_when_needed(store, monkeypatch):
    monkeypatch.setattr(policy, "VERIFICATION_MODE", "name_plus_dob")
    c = make_call(store, "C002")
    confirmed(c)
    assert confirmed(c)["next"] == "ask_date_of_birth"


def test_only_one_link_after_a_promise(store):
    c = make_call(store, "C002")
    confirmed(c)
    assert c.record_promise_to_pay(30, 10, 2026)["saved"] is True
    c.heard("SMS pe bhej do")
    assert c.send_payment_link("sms")["sent"] is True
    c.heard("WhatsApp pe bhi bhejo")
    r = c.send_payment_link("whatsapp")
    assert r == {"sent": False, "already_sent_on": "sms", "to_number_ending": "0002",
                 "reason": "already_sent_after_promise",
                 "next": "say_the_link_was_already_sent_on_that_channel_and_close"}


def test_link_before_promise_still_allows_one_after(store):
    c = make_call(store, "C002")
    confirmed(c)
    c.heard("SMS kar dijiye")
    assert c.send_payment_link("sms")["sent"] is True
    c.record_promise_to_pay(30, 10, 2026)
    c.heard("WhatsApp pe bhi")
    assert c.send_payment_link("whatsapp")["sent"] is True
    assert c.send_payment_link("sms")["sent"] is False


@pytest.mark.parametrize("setup, expected", [
    (lambda c: None, "no_resolution"),
    (lambda c: c.report_person("third_party"), "third_party"),
    (lambda c: c.report_person("unavailable"), "not_available"),
    (lambda c: (confirmed(c), c.record_promise_to_pay(30, 10, 2026)), "promise_to_pay"),
    (lambda c: (confirmed(c), c.transfer_to_human("hardship")), "human_callback"),
    (lambda c: (confirmed(c), c.heard("kal 4 baje"),
                c.schedule_callback(29, 10, 2026, 16, 0, "customer_requested")), "callback_requested"),
])
def test_missing_outcome_filled_in_from_records(store, setup, expected):
    c = make_call(store, "C002")
    setup(c)
    if expected == "human_callback":
        c.outcome = None  # pretend transfer_to_human had not set it
    assert c.finalize_outcome() is True
    assert c.outcome == expected and "filled in by code" in c.note


def test_recorded_outcome_is_kept(store):
    c = make_call(store, "C002")
    confirmed(c)
    c.record_outcome("refused")
    assert c.finalize_outcome() is False and c.outcome == "refused"


def test_records_win_over_a_weaker_outcome(store):
    c = make_call(store, "C002")
    confirmed(c)
    c.record_promise_to_pay(30, 10, 2026)
    r = c.record_outcome("no_resolution")
    assert r == {"saved": False, "error": "records_show_promise_to_pay", "use": "promise_to_pay"}
    assert c.record_outcome("promise_to_pay")["saved"] is True
    assert c.record_outcome("human_callback")["saved"] is True  # a real handover is still allowed


def test_paid_now_without_link_points_to_already_paid(store):
    c = make_call(store, "C003")
    confirmed(c)
    assert c.check_payment_status()["status"] == "paid"
    assert c.record_outcome("paid_now")["use"] == "already_paid_confirmed"
    assert c.finalize_outcome() is True and c.outcome == "already_paid_confirmed"


# ---- step 2: no actions nobody asked for ----

@pytest.mark.parametrize("said", ["okay", "haan", "हां भेज दो"])
def test_link_refused_without_a_named_channel(store, said):
    c = make_call(store, "C002")
    confirmed(c)
    c.heard(said)
    assert c.send_payment_link("sms") == {
        "sent": False, "reason": "caller_did_not_name_a_channel", "next": "ask_sms_or_whatsapp"}
    assert store.links_sent_in_session("s1") == 0


def test_link_goes_on_the_channel_the_caller_named(store):
    c = make_call(store, "C002")
    confirmed(c)
    c.heard("WhatsApp pe bhej do")
    r = c.send_payment_link("sms")  # the AI picked the wrong one; the caller's word wins
    assert r["sent"] is True and r["channel"] == "whatsapp"
    assert store.latest_link("C002")["channel"] == "whatsapp"


@pytest.mark.parametrize("said", ["okay", "haan", "मेरी बहन हैं"])
def test_callback_refused_without_a_time_from_the_caller(store, said):
    c = make_call(store, "C002")
    c.report_person("third_party")
    c.heard(said)
    r = c.schedule_callback(29, 10, 2026, 18, 0, "customer_unavailable")
    assert r == {"saved": False, "reason": "caller_did_not_give_a_time",
                 "next": "ask_the_caller_for_a_day_and_time"}
    assert c.callback_id is None


def test_callback_scheduled_when_the_caller_gives_a_time(store):
    c = make_call(store, "C002")
    c.report_person("third_party")
    c.heard("kal shaam 6 baje")
    r = c.schedule_callback(29, 10, 2026, 18, 0, "customer_unavailable")
    assert r["saved"] is True and r["when"].startswith("2026-10-29T18:00")


# ---- step 4b: no colleague for a third party ----

def test_no_colleague_handover_for_a_third_party(store):
    c = make_call(store, "C002")
    c.report_person("third_party")
    assert c.transfer_to_human("info_not_available") == {
        "requested": False, "reason": "not_the_customer",
        "next": "say_we_will_try_the_customer_again_later"}
    assert c.handoff_requested is False and c.handoff_callback_id is None


# ---- step 8: the date to say has no year ----

def test_promise_gives_a_date_without_the_year(store):
    c = make_call(store, "C002")
    confirmed(c)
    assert c.record_promise_to_pay(30, 10, 2026)["say_date"] == "30 October"


# ---- a third party can't slip past the date-of-birth check ----

def test_third_party_cannot_become_unavailable(store):
    c = make_call(store, "C002")
    c.report_person("third_party")
    assert c.report_person("unavailable") == {
        "ok": False, "reason": "already_third_party", "next": "ask_when_to_reach_the_customer"}
    assert c.person_status == "third_party"


def test_third_party_then_customer_always_needs_dob(store):
    # the gap found in the sister test: third_party -> unavailable -> customer skipped the DOB
    c = make_call(store, "C002")
    c.report_person("third_party")
    c.report_person("unavailable")
    c.heard("actually main hi Sunita hoon")
    r = confirmed(c)
    assert r == {"ok": True, "next": "ask_date_of_birth"}
    assert c.get_due_details() == {"error": "not_unlocked"}
    c.heard("15 March 1990")
    assert c.verify_identity(15, 3, 1990) == {"verified": False, "attempts_left": 2}
    assert c.get_due_details() == {"error": "not_unlocked"}


def test_customer_who_steps_away_is_still_unlocked_on_return(store):
    c = make_call(store, "C002")
    c.report_person("unavailable")
    assert confirmed(c)["next"] == "give_reminder"


def test_okay_from_a_third_party_does_not_make_them_the_customer(store):
    c = make_call(store, "C002")
    c.report_person("third_party")
    c.heard("okay")
    assert confirmed(c) == {
        "ok": False, "reason": "caller_did_not_say_they_are_the_customer",
        "next": "ask_when_to_reach_the_customer"}
    assert c.person_status == "third_party" and c.person_corrections == 0


def test_invented_date_of_birth_is_not_checked(store):
    c = make_call(store, "C002")
    c.report_person("third_party")
    c.heard("actually main hi Sunita hoon")
    confirmed(c)
    r = c.verify_identity(1, 1, 2000)  # the caller hasn't said a date yet
    assert r == {"verified": False, "reason": "caller_did_not_say_a_date",
                 "next": "ask_for_date_of_birth"}
    assert c.dob_attempts == 0
    c.heard("15 March 1990")
    assert c.verify_identity(15, 3, 1990) == {"verified": False, "attempts_left": 2}


def test_third_party_call_is_logged_as_third_party(store):
    c = make_call(store, "C002")
    c.report_person("third_party")
    assert c.record_outcome("not_available") == {
        "saved": False, "error": "someone_else_answered", "use": "third_party"}
    assert c.record_outcome("third_party")["saved"] is True


# ---- batch 2, item 1: a promise ends the payment push ----

def promised(store):
    c = make_call(store, "C002")
    confirmed(c)
    assert c.record_promise_to_pay(30, 10, 2026)["saved"] is True
    return c


def test_link_after_a_promise_says_close(store):
    c = promised(store)
    c.heard("SMS pe bhej do")
    r = c.send_payment_link("sms")
    assert r["sent"] is True and r["next"] == "say_link_sent_remind_date_and_close"
    assert c.link_sent_after_promise is True


def test_link_before_a_promise_asks_to_pay(store):
    c = make_call(store, "C002")
    confirmed(c)
    c.heard("SMS")
    assert c.send_payment_link("sms")["next"] == "ask_caller_to_pay_then_tell_you"


def test_pending_after_a_promise_closes_never_offers_a_callback(store):
    c = promised(store)
    c.heard("SMS")
    c.send_payment_link("sms")
    for _ in range(3):
        assert c.check_payment_status() == {"status": "pending", "next": "remind_promise_date_and_close"}


@pytest.mark.parametrize("reason", ["customer_upset", "refused_to_pay", "payment_not_found",
                                    "info_not_available", "tool_error"])
def test_handover_refused_after_a_promise(store, reason):
    c = promised(store)
    r = c.transfer_to_human(reason)
    assert r["requested"] is False and r["reason"] == "promise_already_recorded"
    assert c.handoff_requested is False and store.callbacks_due(FAR) == []


@pytest.mark.parametrize("reason", ["legal_question", "hardship", "dispute"])
def test_handover_still_allowed_after_a_promise(store, reason):
    c = promised(store)
    assert c.transfer_to_human(reason)["requested"] is True


def test_asking_for_a_person_needs_the_callers_words(store):
    c = promised(store)
    c.heard("okay")
    assert c.transfer_to_human("customer_asked_for_person")["reason"] == "caller_did_not_ask_for_a_person"
    c.heard("kisi insaan se baat karao")
    assert c.transfer_to_human("customer_asked_for_person")["requested"] is True


def test_free_text_handover_reason_is_refused(store):
    c = make_call(store, "C002")
    confirmed(c)
    r = c.transfer_to_human("customer reports hardship")
    assert r["requested"] is False and r["error"] == "invalid_reason"


def test_code_outcome_uses_the_better_label(store):
    c = promised(store)
    assert c.record_outcome_or_better("no_resolution", "customer silent") == {"saved": True}
    assert c.outcome == "promise_to_pay"


# ---- batch 3: a bare "okay" doesn't confirm identity ----

@pytest.mark.parametrize("said", ["okay", "kaun bol raha hai?", "hmm"])
def test_customer_needs_a_clear_yes(store, said):
    c = make_call(store, "C002")
    c.heard(said)
    r = c.report_person("customer")
    assert r == {"ok": False, "reason": "caller_did_not_confirm_identity", "next": "confirm_identity_again"}
    assert c.person_status is None and c.unlocked is False


def test_nothing_said_yet_does_not_confirm(store):
    assert make_call(store, "C002").report_person("customer")["next"] == "confirm_identity_again"


def test_okay_then_haan_confirms(store):
    c = make_call(store, "C002")
    c.heard("okay")
    c.report_person("customer")
    c.heard("haan ji, boliye")
    assert c.report_person("customer")["next"] == "give_reminder"


def test_third_party_and_unavailable_need_no_yes(store):
    c = make_call(store, "C002")
    c.heard("okay")
    assert c.report_person("unavailable")["ok"] is True


# ---- batch 3: no link before an agreed promise is saved ----

READ_BACK = "ठीक है, शुक्रवार 30 October को pay करेंगे, सही?"


def test_link_waits_for_an_agreed_promise(store):
    c = make_call(store, "C002")
    confirmed(c)
    c.heard("हाँ, WhatsApp पे भेज दो")
    assert c.caller_agreed_to(READ_BACK, "हाँ, WhatsApp पे भेज दो") == date(2026, 10, 30)
    r = c.send_payment_link("whatsapp")
    assert r == {"sent": False, "reason": "record_promise_first", "date": "2026-10-30",
                 "next": "record_promise_first"}
    assert c.link_waiting is True and store.latest_link("C002") is None
    assert c.record_promise_to_pay(30, 10, 2026)["saved"] is True
    assert c.send_payment_link("whatsapp")["sent"] is True


def test_the_due_date_is_not_a_read_back(store):
    c = make_call(store, "C002")
    confirmed(c)
    c.heard("haan, SMS kar do")
    assert c.caller_agreed_to("आपकी EMI 10 September को due थी।", "haan, SMS kar do") is None
    assert c.send_payment_link("sms")["sent"] is True


def test_no_to_the_read_back_does_not_block_the_link(store):
    c = make_call(store, "C002")
    confirmed(c)
    assert c.caller_agreed_to(READ_BACK, "नहीं, अभी SMS पे भेज दो") is None
    c.heard("नहीं, अभी SMS पे भेज दो")
    assert c.send_payment_link("sms")["sent"] is True


def test_a_failed_promise_stops_the_wait(store):
    c = make_call(store, "C002")
    confirmed(c)
    c.caller_agreed_to("तो 20 November, सही?", "haan, SMS")
    c.heard("haan, SMS")
    assert c.send_payment_link("sms")["reason"] == "record_promise_first"
    assert c.record_promise_to_pay(20, 11, 2026)["reason"] == "too_far"
    assert c.link_waiting is False and c.send_payment_link("sms")["sent"] is True


def test_a_new_agreed_date_after_a_promise_must_be_saved_before_the_link(store):
    c = make_call(store, "C002")
    confirmed(c)
    c.record_promise_to_pay(30, 10, 2026)
    assert c.caller_agreed_to("तो 1 November, सही?", "haan, SMS pe") == date(2026, 11, 1)
    c.heard("haan, SMS pe")
    assert c.send_payment_link("sms")["reason"] == "record_promise_first"
    c.record_promise_to_pay(1, 11, 2026)
    assert c.send_payment_link("sms")["sent"] is True and c.promise["date"] == date(2026, 11, 1)


def test_agreeing_to_the_saved_promise_date_changes_nothing(store):
    c = make_call(store, "C002")
    confirmed(c)
    c.record_promise_to_pay(30, 10, 2026)
    assert c.caller_agreed_to("तो 30 October, सही?", "haan, SMS pe") is None
    c.heard("haan, SMS pe")
    assert c.send_payment_link("sms")["sent"] is True
