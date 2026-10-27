from datetime import date, datetime, timedelta

import pytest

from tools.rules import IST
from tools.store import MemoryStore

NOW = datetime(2026, 10, 28, 11, 0, tzinfo=IST)


@pytest.fixture
def store():
    return MemoryStore.from_files(clock=lambda: NOW)


def test_customer_merges_seed_defaults(store):
    assert store.get_customer("C001")["language"] == "hi"
    assert store.get_customer("C001")["do_not_call"] is False
    assert store.get_customer("C003")["language"] == "en"
    assert store.get_customer("C010")["do_not_call"] is True
    assert store.get_customer("C999") is None


def test_customer_copy_is_independent(store):
    c = store.get_customer("C001")
    c["name"] = "Changed"
    assert store.get_customer("C001")["name"] == "Rahul Sharma"


def test_seed_promises_loaded(store):
    ids = {p["customer_id"] for p in store.promises_due_on(date(2026, 10, 27))}
    assert ids == {"C011", "C003"}


def test_duplicate_promise_not_created(store):
    first = store.add_promise("C001", date(2026, 10, 30), 4500, "s1")
    again = store.add_promise("C001", date(2026, 10, 30), 4500, "s2")
    assert first["duplicate"] is False and again["duplicate"] is True
    assert len(store.promises_for("C001")) == 1


def test_promise_status_change(store):
    p = store.promises_for("C011")[0]
    store.set_promise_status(p["id"], "kept")
    assert store.promises_due_on(date(2026, 10, 27)) == [
        q for q in store.promises_due_on(date(2026, 10, 27)) if q["customer_id"] != "C011"]
    with pytest.raises(ValueError):
        store.set_promise_status(p["id"], "nonsense")


def test_attempts_recorded(store):
    store.add_attempt("C002", NOW, "s1")
    store.add_attempt("C002", NOW + timedelta(hours=1), "s2")
    assert len(store.attempt_times("C002")) == 2
    assert store.attempt_times("C001") == []


def test_payment_link_flow(store):
    link = store.add_payment_link("C002", 2200, "sms", "s1")
    assert link["status"] == "sent" and link["url"].startswith("https://pay.example.test/")
    paid = store.mark_link_paid(link["id"])
    assert paid["status"] == "paid" and paid["paid_at"] == NOW
    assert store.latest_link("C002")["status"] == "paid"
    assert store.mark_link_paid("PL9999") is None


def test_invalid_channel_rejected(store):
    with pytest.raises(ValueError):
        store.add_payment_link("C002", 2200, "carrier-pigeon", "s1")


def test_links_counted_per_session(store):
    store.add_payment_link("C002", 2200, "sms", "A")
    store.add_payment_link("C002", 2200, "whatsapp", "A")
    store.add_payment_link("C004", 3200, "sms", "B")
    assert store.links_sent_in_session("A") == 2
    assert store.links_sent_in_session("B") == 1
    assert store.links_sent_in_session("C") == 0


def test_payments_feed(store):
    assert store.payments_for("C004")[0]["status"] == "reconciling"
    assert store.payments_for("C001") == []


def test_callbacks_due(store):
    store.add_callback("C001", NOW - timedelta(minutes=5), "earlier", "s1")
    store.add_callback("C001", NOW + timedelta(hours=2), "later", "s1")
    due = store.callbacks_due(NOW)
    assert [c["reason"] for c in due] == ["earlier"]
    store.set_callback_status(due[0]["id"], "done")
    assert store.callbacks_due(NOW) == []


def test_stores_are_isolated():
    a = MemoryStore.from_files(clock=lambda: NOW)
    b = MemoryStore.from_files(clock=lambda: NOW)
    a.add_promise("C001", date(2026, 10, 30), 4500)
    assert b.promises_for("C001") == []