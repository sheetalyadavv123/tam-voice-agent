from datetime import date, datetime

import pytest

from tests.helpers import confirmed
from tools import policy, simulator
from tools.collection_tools import Call
from tools.followups import run_due_followups
from tools.rules import IST
from tools.store import MemoryStore


def ist(day, hour=10, minute=0, month=10):
    return datetime(2026, month, day, hour, minute, tzinfo=IST)


NOW = ist(28, 11)


@pytest.fixture(autouse=True)
def default_mode(monkeypatch):
    monkeypatch.setattr(policy, "VERIFICATION_MODE", "name_only")


@pytest.fixture
def store():
    return MemoryStore.from_files(clock=lambda: NOW)


def for_cust(actions, cid):
    return [a for a in actions if a["customer_id"] == cid]


def kinds(actions):
    return [a["action"] for a in actions]


# ---------- promises ----------
def test_paid_promise_marked_kept(store):
    acts = for_cust(run_due_followups(store, ist(27, 10)), "C003")
    assert kinds(acts) == ["mark_kept"]
    assert store.promises_for("C003", "kept") != []


def test_unpaid_promise_gets_followup_call(store):
    acts = for_cust(run_due_followups(store, ist(27, 10)), "C011")
    assert kinds(acts) == ["call"] and acts[0]["kind"] == "followup"
    assert len(store.attempt_times("C011")) == 1


def test_no_duplicate_call_on_second_run(store):
    run_due_followups(store, ist(27, 10))
    assert run_due_followups(store, ist(27, 10, 5)) == []


def test_future_promise_left_alone(store):
    store.add_promise("C002", date(2026, 10, 30), 2200)
    assert for_cust(run_due_followups(store, ist(28, 10)), "C002") == []


def test_outside_window_defers_to_next_opening(store):
    acts = for_cust(run_due_followups(store, ist(27, 20, 30)), "C011")
    assert kinds(acts) == ["defer"] and acts[0]["when"].startswith("2026-10-28T08:00")
    assert store.attempt_times("C011") == []


def test_before_window_defers_to_same_day_opening(store):
    acts = for_cust(run_due_followups(store, ist(27, 6)), "C011")
    assert kinds(acts) == ["defer"] and acts[0]["when"].startswith("2026-10-27T08:00")


def test_grace_day_then_broken(store):
    assert kinds(for_cust(run_due_followups(store, ist(28, 9)), "C011")) == ["call"]
    assert kinds(for_cust(run_due_followups(store, ist(29, 9)), "C011")) == ["mark_broken"]
    assert store.promises_for("C011", "broken") != []


def test_reconciling_payment_waits(store):
    store.add_promise("C004", date(2026, 10, 28), 3200)
    assert kinds(for_cust(run_due_followups(store, ist(28, 10)), "C004")) == ["wait"]


def test_partial_payment_still_called(store):
    simulator.receive_payment(store, "C005", 1000, "2026-10-25")
    store.add_promise("C005", date(2026, 10, 28), 5100)
    assert kinds(for_cust(run_due_followups(store, ist(28, 10)), "C005")) == ["call"]


def test_do_not_call_customer_skipped(store):
    store.add_promise("C010", date(2026, 10, 28), 3800)
    acts = for_cust(run_due_followups(store, ist(28, 10)), "C010")
    assert acts[0]["action"] == "skip" and acts[0]["reason"] == "do_not_call"
    assert store.attempt_times("C010") == []


def test_daily_limit_blocks_call(store):
    store.add_attempt("C011", ist(27, 8), kind="reminder")
    store.add_attempt("C011", ist(27, 9), kind="reminder")
    acts = for_cust(run_due_followups(store, ist(27, 10)), "C011")
    assert acts[0]["action"] == "skip" and acts[0]["reason"] == "daily_limit"


def test_paying_the_link_marks_promise_kept(store):
    store.add_promise("C002", date(2026, 10, 28), 2200)
    store.add_payment_link("C002", 2200, "sms", "s1")
    assert simulator.pay_link(store, "C002")["ok"] is True
    assert kinds(for_cust(run_due_followups(store, ist(28, 12)), "C002")) == ["mark_kept"]


# ---------- callbacks ----------
def test_due_callback_calls_customer_once(store):
    store.add_callback("C001", ist(28, 10), "customer_unavailable")
    acts = for_cust(run_due_followups(store, NOW), "C001")
    assert kinds(acts) == ["call"] and acts[0]["kind"] == "callback"
    assert store.callbacks_due(NOW) == []
    assert for_cust(run_due_followups(store, ist(28, 11, 5)), "C001") == []


def test_human_callback_routed_to_humans(store):
    store.add_callback("C008", ist(28, 10), "human_followup")
    acts = for_cust(run_due_followups(store, NOW), "C008")
    assert kinds(acts) == ["route_to_human"]
    assert store.callbacks_due(NOW) == []


def test_callback_outside_window_stays_open(store):
    store.add_callback("C001", ist(28, 20), "customer_requested")
    late = ist(28, 20, 30)
    assert kinds(for_cust(run_due_followups(store, late), "C001")) == ["defer"]
    assert len(store.callbacks_due(late)) == 1


def test_callback_for_do_not_call_cancelled(store):
    store.add_callback("C010", ist(28, 9), "customer_requested")
    acts = for_cust(run_due_followups(store, NOW), "C010")
    assert acts[0]["action"] == "skip" and acts[0]["reason"] == "do_not_call"
    assert store.callbacks_due(NOW) == []


def test_callback_merges_into_followup_call(store):
    store.add_callback("C011", ist(27, 9), "customer_requested")
    acts = for_cust(run_due_followups(store, ist(27, 10)), "C011")
    assert sorted(kinds(acts)) == ["call", "merged_with_followup"]


# ---------- other pieces ----------
def test_new_promise_cancels_old_one(store):
    c = Call(store, "C011", session_id="s1", clock=lambda: NOW)
    confirmed(c)
    assert c.record_promise_to_pay(29, 10, 2026)["saved"] is True
    open_ones = store.promises_for("C011", "open")
    assert [p["promised_date"] for p in open_ones] == [date(2026, 10, 29)]
    assert len(store.promises_for("C011", "cancelled")) == 1


def test_simulator_payment_and_link(store):
    simulator.receive_payment(store, "C001", 4500, "2026-10-27")
    assert store.payments_for("C001")[0]["status"] == "posted"
    assert simulator.pay_link(store, "C001") == {"ok": False, "reason": "no_link"}