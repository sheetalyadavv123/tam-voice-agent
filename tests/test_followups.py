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


# ---------- wrong number: a person checks the number first ----------
def test_wrong_number_customer_is_not_called_for_a_due_promise(store):
    store.add_attempt("C011", ist(26, 10), "s0", outcome="wrong_number")
    acts = for_cust(run_due_followups(store, ist(27, 10)), "C011")
    assert acts == [{"action": "skip", "reason": "needs_number_check",
                     "customer_id": "C011", "promise_id": acts[0]["promise_id"]}]
    assert [a["outcome"] for a in store.attempts_for("C011")] == ["wrong_number"]  # no new attempt
    assert store.promises_for("C011", "open") != []


def test_wrong_number_customer_callback_stays_open_and_is_not_called(store):
    store.add_attempt("C001", ist(27, 10), "s0", outcome="wrong_number")
    store.add_callback("C001", ist(28, 10), "customer_unavailable")
    acts = for_cust(run_due_followups(store, NOW), "C001")
    assert kinds(acts) == ["skip"] and acts[0]["reason"] == "needs_number_check"
    assert len(store.callbacks_due(NOW)) == 1 and len(store.attempt_times("C001")) == 1


def test_wrong_number_customer_human_callback_is_not_routed(store):
    store.add_attempt("C008", ist(27, 10), "s0", outcome="wrong_number")
    store.add_callback("C008", ist(28, 10), "human_followup")
    acts = for_cust(run_due_followups(store, NOW), "C008")
    assert acts[0]["reason"] == "needs_number_check" and len(store.callbacks_due(NOW)) == 1


def test_wrong_number_customer_promise_is_still_marked_broken(store):
    store.add_attempt("C011", ist(26, 10), "s0", outcome="wrong_number")
    assert kinds(for_cust(run_due_followups(store, ist(29, 9)), "C011")) == ["mark_broken"]


def test_a_later_answered_call_clears_the_number_check(store):
    store.add_attempt("C011", ist(25, 10), "s0", outcome="wrong_number")
    store.add_attempt("C011", ist(26, 10), "s1", outcome="promise_to_pay")
    assert kinds(for_cust(run_due_followups(store, ist(27, 10)), "C011")) == ["call"]


def test_an_attempt_without_an_outcome_does_not_clear_it(store):
    store.add_attempt("C011", ist(25, 10), "s0", outcome="wrong_number")
    store.add_attempt("C011", ist(26, 10), "s1")  # still running, or never finished
    assert for_cust(run_due_followups(store, ist(27, 10)), "C011")[0]["reason"] == "needs_number_check"


def test_a_changed_phone_number_lifts_the_hold(store):
    store.add_attempt("C011", ist(26, 10), "s0", outcome="wrong_number", phone="+91 90000 00000")
    assert store.number_check_hold("C011") is None
    assert kinds(for_cust(run_due_followups(store, ist(27, 10)), "C011")) == ["call"]


def test_the_same_phone_number_keeps_the_hold(store):
    phone = store.get_customer("C011")["phone"]
    store.add_attempt("C011", ist(26, 10), "s0", outcome="wrong_number", phone=phone)
    assert store.number_check_hold("C011")["outcome"] == "wrong_number"


def test_a_number_marked_checked_lifts_the_hold(store):
    store.add_attempt("C011", ist(26, 10), "s0", outcome="wrong_number")
    store._attempts[-1]["number_checked_at"] = ist(26, 12)
    assert store.number_check_hold("C011") is None


def test_the_phone_is_kept_only_as_a_hash(store):
    phone = store.get_customer("C011")["phone"]
    store.add_attempt("C011", ist(26, 10), "s0", phone=phone)
    saved = store.attempts_for("C011")[0]
    assert phone not in str(saved) and len(saved["phone_hash"]) == 64


def _script(name):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent.parent / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # main() only runs from the command line
    return mod


def test_clear_number_check_needs_a_customer_and_an_env_file(capsys):
    mod = _script("clear_number_check")
    assert mod.main(["clear_number_check.py", "C011"]) == 2  # no default env file
    assert "Usage" in capsys.readouterr().out


def test_clear_number_check_only_reads_the_env_file_it_is_given(tmp_path, capsys, monkeypatch):
    mod = _script("clear_number_check")
    connected = []
    monkeypatch.setattr(mod.asyncpg, "connect", lambda url: connected.append(url))
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@from-the-environment.test/db")
    assert mod.main(["x", "C011", str(tmp_path / "missing.env")]) == 1
    empty = tmp_path / "empty.env"
    empty.write_text("APP_ENV=dev\n")
    assert mod.main(["x", "C011", str(empty)]) == 1  # not the DATABASE_URL in the environment
    good = tmp_path / "dev.env"
    good.write_text("DATABASE_URL=postgresql://u:p@db.example.test/db\n")
    assert mod.main(["x", "C999", str(good)]) == 1  # unknown customer: never connects
    out = capsys.readouterr().out
    assert "env file not found" in out and "no DATABASE_URL" in out and "unknown customer C999" in out
    assert connected == []


def test_clear_number_check_says_what_it_changed():
    mod = _script("clear_number_check")
    a = {"id": 12, "attempted_at": ist(26, 10), "outcome": "wrong_number", "number_checked_at": ist(28, 9)}
    assert mod.describe("C011", {"cleared": True, "attempt": a}) == (
        "cleared: C011 wrong-number hold lifted. call_attempts id 12 (call at 2026-10-26 10:00 UTC+05:30) "
        "number_checked_at set to 2026-10-28 09:00 UTC+05:30. The scheduler may call this customer again.")
    assert mod.describe("C011", {"cleared": False, "reason": "already_checked", "attempt": a}) == (
        "nothing changed: C011 has no wrong-number hold (the wrong-number call at 2026-10-26 10:00 "
        "UTC+05:30 was already marked checked).")
    assert mod.describe("C011", {"cleared": False, "reason": "no_finished_attempts"}).startswith(
        "nothing changed: C011 has no wrong-number hold (no finished calls")


def test_run_followups_lists_number_checks_once_each():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "run_followups", Path(__file__).parent.parent / "scripts" / "run_followups.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # main() only runs from the command line
    acts = [{"action": "skip", "reason": "needs_number_check", "customer_id": "C011"},
            {"action": "call", "kind": "callback", "customer_id": "C001"},
            {"action": "skip", "reason": "needs_number_check", "customer_id": "C011"},
            {"action": "skip", "reason": "needs_number_check", "customer_id": "C004"}]
    assert mod.number_checks(acts) == ["C011", "C004"]


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