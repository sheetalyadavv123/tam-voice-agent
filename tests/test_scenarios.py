import inspect
from datetime import datetime
from pathlib import Path

import pytest

from tools import simulator
from tools.collection_tools import Call
from tools.followups import run_due_followups
from tools.rules import IST, can_call_now
from tools.scenario_runner import NOW, TOOLS, load_scenarios, run_scenario
from tools.store import MemoryStore

SCENARIOS = load_scenarios(Path(__file__).parent / "scenarios.yaml")
FORBIDDEN = {"customer_id", "phone", "number", "amount", "url", "link"}


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["name"] for s in SCENARIOS])
def test_scenario(scenario):
    result = run_scenario(scenario)
    assert result["failures"] == [], "\n".join(result["failures"])


def test_tools_never_take_customer_phone_or_amount():
    """The AI can only act on the customer it is calling, and can't pick numbers or amounts."""
    for name in TOOLS:
        params = set(inspect.signature(getattr(Call, name)).parameters) - {"self"}
        assert not (params & FORBIDDEN), f"{name} accepts {params & FORBIDDEN}"


def test_pre_call_gate():
    store = MemoryStore.from_files(clock=lambda: NOW)
    assert can_call_now(store.get_customer("C010"), [], NOW)["reason"] == "do_not_call"
    night = datetime(2026, 10, 28, 20, 30, tzinfo=IST)
    assert can_call_now(store.get_customer("C001"), [], night)["reason"] == "outside_window"
    assert can_call_now(store.get_customer("C001"), [], NOW)["allowed"] is True


def test_promise_then_followup_paid():
    moment = {"now": datetime(2026, 10, 26, 10, 0, tzinfo=IST)}

    def clock():
        return moment["now"]

    def mine(actions):
        return [a["action"] for a in actions if a["customer_id"] == "C002"]

    store = MemoryStore.from_files(clock=clock)
    first = Call(store, "C002", session_id="r1", clock=clock)
    first.report_person("customer")
    assert first.record_promise_to_pay(28, 10, 2026)["saved"] is True
    first.record_outcome("promise_to_pay")

    moment["now"] = datetime(2026, 10, 27, 10, 0, tzinfo=IST)
    assert mine(run_due_followups(store, clock())) == []

    moment["now"] = datetime(2026, 10, 28, 10, 0, tzinfo=IST)
    assert mine(run_due_followups(store, clock())) == ["call"]

    follow = Call(store, "C002", session_id="f1", clock=clock)
    follow.report_person("customer")
    assert follow.get_due_details()["open_promise"] == {"date": "2026-10-28", "amount": 2200}
    follow.heard("SMS bhej do")
    follow.send_payment_link("sms")
    simulator.pay_link(store, "C002")
    assert follow.check_payment_status()["status"] == "paid"
    assert follow.record_outcome("paid_now")["saved"] is True

    moment["now"] = datetime(2026, 10, 28, 15, 0, tzinfo=IST)
    assert mine(run_due_followups(store, clock())) == ["mark_kept"]


def test_runner_catches_a_wrong_expectation():
    """Proves a scenario can actually fail, so a pass means something."""
    bad = {"name": "bad", "customer": "C002", "steps": [
        {"call": "report_person", "args": {"status": "customer"}, "expect": {"ok": False}}]}
    assert run_scenario(bad)["failures"] != []