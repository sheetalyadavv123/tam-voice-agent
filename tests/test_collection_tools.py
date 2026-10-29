from datetime import datetime

import pytest

from tools import policy
from tools.collection_tools import Call
from tools.rules import IST
from tools.store import MemoryStore

NOW = datetime(2026, 10, 28, 11, 0, tzinfo=IST)


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
    assert c.report_person("customer")["next"] == "ask_date_of_birth"
    assert c.get_due_details() == {"error": "not_unlocked"}


def test_name_only_unlocks_reminder(store):
    c = make_call(store, "C002")
    assert c.report_person("customer")["next"] == "give_reminder"
    d = c.get_due_details()
    assert d["emi_amount"] == 2200 and d["overdue_days"] == 44 and d["due_date"] == "2026-09-10"
    assert "outstanding" not in d and "dob" not in d


def test_name_only_verify_not_required(store):
    c = make_call(store)
    c.report_person("customer")
    assert c.verify_identity(2, 11, 1985) == {"verified": True, "not_required": True}


def test_verify_needs_person_first(store):
    assert make_call(store).verify_identity(2, 11, 1985) == {"error": "confirm_person_first"}


def test_dob_mode_requires_dob(store, monkeypatch):
    monkeypatch.setattr(policy, "VERIFICATION_MODE", "name_plus_dob")
    c = make_call(store, "C002")
    assert c.report_person("customer")["next"] == "ask_date_of_birth"
    assert c.get_due_details() == {"error": "not_unlocked"}
    assert c.verify_identity(1, 1, 2000) == {"verified": False, "attempts_left": 2}
    assert c.verify_identity(2, 11, 1985)["verified"] is True
    assert c.get_due_details()["emi_amount"] == 2200


def test_dob_lockout(store, monkeypatch):
    monkeypatch.setattr(policy, "VERIFICATION_MODE", "name_plus_dob")
    c = make_call(store, "C002")
    c.report_person("customer")
    c.verify_identity(1, 1, 2000)
    c.verify_identity(2, 2, 2000)
    assert c.verify_identity(3, 3, 2000)["locked"] is True
    assert c.handoff_requested is True
    assert c.verify_identity(2, 11, 1985)["locked"] is True
    assert c.get_due_details() == {"error": "not_unlocked"}


def test_impossible_dates_do_not_use_attempts(store, monkeypatch):
    monkeypatch.setattr(policy, "VERIFICATION_MODE", "name_plus_dob")
    c = make_call(store, "C002")
    c.report_person("customer")
    assert c.verify_identity(31, 2, 1990)["error"] == "impossible_date"
    assert c.verify_identity(31, 2, 1990)["error"] == "impossible_date"
    assert c.verify_identity(1, 1, 2000) == {"verified": False, "attempts_left": 2}


def test_impossible_dates_cap_hands_over(store, monkeypatch):
    monkeypatch.setattr(policy, "VERIFICATION_MODE", "name_plus_dob")
    c = make_call(store, "C002")
    c.report_person("customer")
    c.verify_identity(31, 2, 1990)
    c.verify_identity(31, 2, 1990)
    assert c.verify_identity(31, 2, 1990)["locked"] is True
    assert c.handoff_requested is True and c.dob_attempts == 0


def test_two_digit_year_rejected(store, monkeypatch):
    monkeypatch.setattr(policy, "VERIFICATION_MODE", "name_plus_dob")
    c = make_call(store, "C002")
    c.report_person("customer")
    assert c.verify_identity(2, 11, 85)["error"] == "impossible_date"


def test_closed_loan(store):
    c = make_call(store, "C009")
    c.report_person("customer")
    d = c.get_due_details()
    assert d["loan_closed"] is True and "emi_amount" not in d


def test_open_promise_shown(store):
    c = make_call(store, "C011")
    c.report_person("customer")
    assert c.get_due_details()["open_promise"] == {"date": "2026-10-27", "amount": 5400}

# ---- rules moved from the prompt into code ----

def test_reminder_facts_come_with_report_person(store):
    c = make_call(store, "C002")
    r = c.report_person("customer")
    assert r["next"] == "give_reminder"
    assert r["due"]["emi_amount"] == 2200 and r["due"]["due_date"] == "2026-09-10"


def test_reminder_facts_come_with_verified_dob(store, monkeypatch):
    monkeypatch.setattr(policy, "VERIFICATION_MODE", "name_plus_dob")
    c = make_call(store, "C002")
    assert "due" not in c.report_person("customer")
    r = c.verify_identity(2, 11, 1985)
    assert r["verified"] is True and r["due"]["emi_amount"] == 2200


def test_repeated_customer_report_is_harmless(store):
    c = make_call(store, "C002")
    c.report_person("customer")
    r = c.report_person("customer")
    assert r == {"ok": True, "duplicate": True, "next": "continue_the_conversation"}
    assert c.unlocked


def test_repeated_customer_report_still_asks_dob_when_needed(store, monkeypatch):
    monkeypatch.setattr(policy, "VERIFICATION_MODE", "name_plus_dob")
    c = make_call(store, "C002")
    c.report_person("customer")
    assert c.report_person("customer")["next"] == "ask_date_of_birth"


def test_only_one_link_after_a_promise(store):
    c = make_call(store, "C002")
    c.report_person("customer")
    assert c.record_promise_to_pay(30, 10, 2026)["saved"] is True
    assert c.send_payment_link("sms")["sent"] is True
    r = c.send_payment_link("whatsapp")
    assert r == {"sent": False, "reason": "already_sent_after_promise", "next": "close_the_call"}


def test_link_before_promise_still_allows_one_after(store):
    c = make_call(store, "C002")
    c.report_person("customer")
    assert c.send_payment_link("sms")["sent"] is True
    c.record_promise_to_pay(30, 10, 2026)
    assert c.send_payment_link("whatsapp")["sent"] is True
    assert c.send_payment_link("sms")["sent"] is False


@pytest.mark.parametrize("setup, expected", [
    (lambda c: None, "no_resolution"),
    (lambda c: c.report_person("third_party"), "third_party"),
    (lambda c: c.report_person("unavailable"), "not_available"),
    (lambda c: (c.report_person("customer"), c.record_promise_to_pay(30, 10, 2026)), "promise_to_pay"),
    (lambda c: (c.report_person("customer"), c.transfer_to_human("hardship")), "human_callback"),
    (lambda c: (c.report_person("customer"),
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
    c.report_person("customer")
    c.record_outcome("refused")
    assert c.finalize_outcome() is False and c.outcome == "refused"


def test_records_win_over_a_weaker_outcome(store):
    c = make_call(store, "C002")
    c.report_person("customer")
    c.record_promise_to_pay(30, 10, 2026)
    r = c.record_outcome("no_resolution")
    assert r == {"saved": False, "error": "records_show_promise_to_pay", "use": "promise_to_pay"}
    assert c.record_outcome("promise_to_pay")["saved"] is True
    assert c.record_outcome("human_callback")["saved"] is True  # a real handover is still allowed


def test_paid_now_without_link_points_to_already_paid(store):
    c = make_call(store, "C003")
    c.report_person("customer")
    assert c.check_payment_status()["status"] == "paid"
    assert c.record_outcome("paid_now")["use"] == "already_paid_confirmed"
    assert c.finalize_outcome() is True and c.outcome == "already_paid_confirmed"
