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
    assert c.report_person("customer")["reason"] == "already_third_party"
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
    assert c.verify_identity(2, 11, 1985) == {"verified": True}
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