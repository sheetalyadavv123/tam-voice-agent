"""Replays scripted calls through the tools. No AI, no voice, no credits."""
from datetime import datetime
from pathlib import Path

import yaml

from tools import simulator
from tools.collection_tools import Call
from tools.rules import IST
from tools.store import MemoryStore

NOW = datetime(2026, 10, 28, 11, 0, tzinfo=IST)
FAR_FUTURE = datetime(2100, 1, 1, tzinfo=IST)
TOOLS = ["report_person", "verify_identity", "get_due_details", "send_payment_link",
         "check_payment_status", "record_promise_to_pay", "schedule_callback",
         "record_outcome", "transfer_to_human"]


def load_scenarios(path):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def matches(actual, expected):
    """Every key in `expected` must be in `actual` with an equal value (nesting allowed)."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            k in actual and matches(actual[k], v) for k, v in expected.items())
    return actual == expected


def _world(store, customer_id, step):
    kind = step["world"]
    if kind == "pay_link":
        return simulator.pay_link(store, customer_id)
    if kind == "receive_payment":
        return simulator.receive_payment(store, customer_id, step["amount"],
                                         step["received_on"], step.get("status", "posted"))
    raise ValueError(f"unknown world event: {kind}")


def _check_final(name, call, store, final):
    problems = []
    if "summary" in final and not matches(call.summary(), final["summary"]):
        problems.append(f"{name} final summary: expected {final['summary']}, got {call.summary()}")
    for status, count in final.get("promises", {}).items():
        got = len(store.promises_for(call.customer_id, status))
        if got != count:
            problems.append(f"{name} final: expected {count} {status} promises, got {got}")
    if "callbacks_open" in final:
        got = len(store.callbacks_due(FAR_FUTURE))
        if got != final["callbacks_open"]:
            problems.append(f"{name} final: expected {final['callbacks_open']} open callbacks, got {got}")
    return problems


def run_scenario(sc, now=NOW):
    """Returns {"failures": [...], "log": [...]}. An empty failures list means it passed."""
    failures, log = [], []
    store = MemoryStore.from_files(clock=lambda: now)
    call = Call(store, sc["customer"], session_id=sc["name"], clock=lambda: now)
    for number, step in enumerate(sc["steps"], 1):
        if "world" in step:
            _world(store, sc["customer"], step)
            log.append(f"   {number}. (world) {step['world']}")
            continue
        if "hear" in step:
            call.heard(step["hear"])
            log.append(f"   {number}. (caller) {step['hear']}")
            continue
        tool = step["call"]
        if tool not in TOOLS:
            failures.append(f"{sc['name']} step {number}: unknown tool '{tool}'")
            continue
        result = getattr(call, tool)(**step.get("args", {}))
        log.append(f"   {number}. {tool}({step.get('args', {})}) -> {result}")
        if not matches(result, step.get("expect", {})):
            failures.append(
                f"{sc['name']} step {number} {tool}: expected {step.get('expect')}, got {result}")
        for key in step.get("expect_absent", []):
            if key in result:
                failures.append(f"{sc['name']} step {number} {tool}: '{key}' must not appear")
    failures += _check_final(sc["name"], call, store, sc.get("final", {}))
    return {"failures": failures, "log": log}