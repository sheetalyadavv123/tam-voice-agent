from datetime import datetime

import pytest

from tools import policy
from tools.collection_tools import Call
from tools.rules import IST
from tools.store import MemoryStore

NOW = datetime(2026, 10, 28, 11, 0, tzinfo=IST)
FAR = datetime(2026, 12, 1, tzinfo=IST)


@pytest.fixture(autouse=True)
def default_mode(monkeypatch):
    monkeypatch.setattr(policy, "VERIFICATION_MODE", "name_only")


@pytest.fixture
def store():
    return MemoryStore.from_files(clock=lambda: NOW)


def make_call(store, cid="C002"):
    return Call(store, cid, session_id="s1", clock=lambda: NOW)


def unlocked_call(store, cid="C002", says="SMS pe bhej dijiye"):
    c = make_call(store, cid)
    c.report_person("customer")
    c.heard(says)
    return c


# ---------- payment link ----------
def test_link_needs_unlock(store):
    assert make_call(store).send_payment_link("sms") == {"error": "not_unlocked"}


def test_link_goes_to_registered_number_and_hides_url(store):
    r = unlocked_call(store).send_payment_link("sms")
    assert r["sent"] is True and r["to_number_ending"] == "0002" and r["amount"] == 2200
    assert "url" not in r
    assert store.links_sent_in_session("s1") == 1


def test_link_invalid_channel(store):
    assert unlocked_call(store).send_payment_link("email")["error"] == "invalid_channel"


def test_link_limit_per_call(store):
    c = unlocked_call(store)
    assert c.send_payment_link("sms")["sent"] is True
    c.heard("WhatsApp pe bhi bhej do")
    assert c.send_payment_link("whatsapp")["sent"] is True
    assert c.send_payment_link("sms") == {
        "sent": False, "already_sent_on": "whatsapp", "to_number_ending": "0002",
        "reason": "limit_reached", "next": "offer_promise_or_callback"}


def test_link_not_sent_for_closed_loan(store):
    assert unlocked_call(store, "C009").send_payment_link("sms")["reason"] == "no_payment_due"


# ---------- payment status ----------
def test_status_pending_then_paid(store):
    c = unlocked_call(store)
    assert c.check_payment_status()["status"] == "none"
    c.send_payment_link("sms")
    assert c.check_payment_status()["next"] == "ask_if_they_have_paid_yet"
    assert c.check_payment_status()["next"] == "offer_promise_or_callback"
    store.mark_link_paid(store.latest_link("C002")["id"])
    r = c.check_payment_status()
    assert r["status"] == "paid" and r["source"] == "payment_link"
    assert c.payment_confirmed is True


def test_status_reconciling(store):
    c = unlocked_call(store, "C004")
    r = c.check_payment_status()
    assert r["status"] == "reconciling" and r["amount"] == 3200
    assert c.prior_payment_seen is True


def test_status_posted_payment_counts_as_paid(store):
    r = unlocked_call(store, "C003").check_payment_status()
    assert r["status"] == "paid" and r["source"] == "company_records"


def test_status_none_when_nothing_found(store):
    assert unlocked_call(store, "C005").check_payment_status()["status"] == "none"


def test_status_ignores_link_from_another_call(store):
    other = store.add_payment_link("C005", 5100, "sms", "other-session")
    store.mark_link_paid(other["id"])
    assert unlocked_call(store, "C005").check_payment_status()["status"] == "none"


def test_status_partial_payment(store):
    store._payments.append({"customer_id": "C005", "amount": 1000,
                            "received_on": "2026-10-25", "status": "posted"})
    r = unlocked_call(store, "C005").check_payment_status()
    assert r["status"] == "partial" and r["next"] == "offer_human_callback"


def test_status_ignores_old_payments(store):
    store._payments.append({"customer_id": "C005", "amount": 5100,
                            "received_on": "2026-09-01", "status": "posted"})
    assert unlocked_call(store, "C005").check_payment_status()["status"] == "none"


# ---------- promise to pay ----------
def test_promise_saved(store):
    r = unlocked_call(store).record_promise_to_pay(30, 10, 2026)
    assert r["saved"] is True and r["date"] == "2026-10-30" and r["amount"] == 2200
    assert len(store.promises_for("C002", "open")) == 1


def test_promise_validation(store):
    c = unlocked_call(store)
    assert c.record_promise_to_pay(27, 10, 2026)["reason"] == "in_the_past"
    r = c.record_promise_to_pay(5, 11, 2026)
    assert r["reason"] == "too_far" and r["next"] == "offer_human_callback"
    assert c.record_promise_to_pay(31, 2, 2026)["reason"] == "impossible_date"
    assert store.promises_for("C002") == []


def test_promise_not_saved_twice(store):
    c = unlocked_call(store)
    c.record_promise_to_pay(30, 10, 2026)
    assert c.record_promise_to_pay(30, 10, 2026)["duplicate"] is True
    assert len(store.promises_for("C002")) == 1


def test_promise_needs_unlock(store):
    assert make_call(store).record_promise_to_pay(30, 10, 2026) == {"error": "not_unlocked"}


# ---------- callbacks ----------
def test_callback_saved_and_replaced(store):
    c = make_call(store, "C006")
    c.report_person("third_party")
    c.heard("kal subah 10 baje kar lena, nahi to shaam 4 baje")
    first = c.schedule_callback(29, 10, 2026, 10, 0, "customer_unavailable")
    assert first["saved"] is True and first["when"].startswith("2026-10-29T10:00")
    assert c.schedule_callback(29, 10, 2026, 16, 0, "customer_unavailable")["saved"] is True
    open_ones = store.callbacks_due(FAR)
    assert len(open_ones) == 1 and open_ones[0]["due_at"].hour == 16


def test_callback_rules(store):
    assert make_call(store).schedule_callback(
        29, 10, 2026, 10, 0, "customer_unavailable") == {"error": "confirm_person_first"}
    c = make_call(store)
    c.report_person("unavailable")
    c.heard("raat 9 baje call karna")
    assert c.schedule_callback(29, 10, 2026, 21, 0, "customer_unavailable")["reason"] == "outside_window"
    assert c.schedule_callback(28, 10, 2026, 9, 0, "customer_unavailable")["reason"] == "in_the_past"
    assert c.schedule_callback(10, 11, 2026, 10, 0, "customer_unavailable")["reason"] == "too_far"
    assert c.schedule_callback(29, 10, 2026, 25, 0, "customer_unavailable")["reason"] == "impossible_time"
    assert c.schedule_callback(29, 10, 2026, 10, 0, "because")["error"] == "invalid_reason"


# ---------- outcomes ----------
def test_outcomes_must_match_records(store):
    c = unlocked_call(store, "C004")
    for bad in ("paid_now", "promise_to_pay", "already_paid_confirmed"):
        assert c.record_outcome(bad)["error"] == "not_supported_by_records"
    assert c.record_outcome("made_up")["error"] == "invalid_outcome"
    assert c.record_outcome("refused", "x" * 500)["saved"] is True
    assert len(c.note) == 200
    c.check_payment_status()
    assert c.record_outcome("already_paid_confirmed")["saved"] is True


def test_outcomes_for_promise_and_payment(store):
    c = unlocked_call(store, "C002")
    c.record_promise_to_pay(30, 10, 2026)
    assert c.record_outcome("promise_to_pay")["saved"] is True
    c2 = unlocked_call(store, "C005")
    c2.send_payment_link("sms")
    store.mark_link_paid(store.latest_link("C005")["id"])
    c2.check_payment_status()
    assert c2.record_outcome("paid_now")["saved"] is True


# ---------- human handover ----------
def test_transfer_creates_one_callback(store):
    c = unlocked_call(store, "C008")
    r = c.transfer_to_human("customer upset")
    assert r["requested"] is True and "colleague" in r["next"]
    c.transfer_to_human("customer upset")
    assert len(store.callbacks_due(FAR)) == 1
    assert c.outcome == "human_callback" and c.note == "customer upset"
    assert c.handoff_requested is True


def test_transfer_keeps_existing_outcome(store):
    c = unlocked_call(store, "C008")
    c.record_outcome("dispute")
    c.transfer_to_human("needs a person")
    assert c.outcome == "dispute"


def test_summary(store):
    s = unlocked_call(store).summary()
    assert s["person_status"] == "customer" and s["outcome"] is None
    assert s["handoff_requested"] is False and s["callback_scheduled"] is False