"""Decides which follow-up calls are due. It places no calls; it returns a list of actions."""
from datetime import timedelta

from tools import policy, rules
from tools.rules import IST

HUMAN_REASONS = {"human_followup", "payment_help"}


def payment_state(store, customer_id, needed):
    """'paid', 'reconciling' or 'none', judged from records only."""
    loan = store.get_customer(customer_id)["loan"]
    link = store.latest_link(customer_id)
    if link and link["status"] == "paid" and link["amount"] >= needed:
        return "paid"
    state = "none"
    for p in store.payments_for(customer_id):
        if p["received_on"] <= loan["last_payment_date"] or p["amount"] < needed:
            continue
        if p["status"] == "posted":
            return "paid"
        state = "reconciling"
    return state


def _followed_up_today(store, customer_id, today):
    return any(a["kind"] == "followup" and a["at"].astimezone(IST).date() == today
               for a in store.attempts_for(customer_id))


def _gate(store, customer_id, now):
    return rules.can_call_now(store.get_customer(customer_id),
                              store.attempt_times(customer_id), now)


def _blocked(verdict, now, **ids):
    if verdict["reason"] == "outside_window":
        return {"action": "defer", "reason": "outside_window",
                "when": rules.next_window_start(now).isoformat(), **ids}
    return {"action": "skip", "reason": verdict["reason"], **ids}


def run_due_followups(store, now, record_attempts=True):
    now = now.astimezone(IST)
    today = now.date()
    actions, planned = [], set()

    # 1. Promises to pay that are due
    for cid in store.customer_ids():
        for p in store.promises_for(cid, "open"):
            ids = {"customer_id": cid, "promise_id": p["id"]}
            if p["promised_date"] > today:
                continue
            state = payment_state(store, cid, p["amount"])
            if state == "paid":
                store.set_promise_status(p["id"], "kept")
                actions.append({"action": "mark_kept", **ids})
                continue
            if state == "reconciling":
                actions.append({"action": "wait", "reason": "payment_reconciling", **ids})
                continue
            if p["promised_date"] < today - timedelta(days=policy.PROMISE_GRACE_DAYS):
                store.set_promise_status(p["id"], "broken")
                actions.append({"action": "mark_broken", **ids})
                continue
            if cid in planned or _followed_up_today(store, cid, today):
                continue
            verdict = _gate(store, cid, now)
            if not verdict["allowed"]:
                actions.append(_blocked(verdict, now, **ids))
                continue
            if record_attempts:
                store.add_attempt(cid, now, kind="followup")
            planned.add(cid)
            actions.append({"action": "call", "kind": "followup", **ids})

    # 2. Callbacks that are due
    for cb in store.callbacks_due(now):
        cid = cb["customer_id"]
        ids = {"customer_id": cid, "callback_id": cb["id"]}
        if cb["reason"] in HUMAN_REASONS:
            store.set_callback_status(cb["id"], "done")
            actions.append({"action": "route_to_human", "reason": cb["reason"], **ids})
            continue
        if cid in planned:
            store.set_callback_status(cb["id"], "done")
            actions.append({"action": "merged_with_followup", **ids})
            continue
        verdict = _gate(store, cid, now)
        if verdict["reason"] == "do_not_call":
            store.set_callback_status(cb["id"], "cancelled")
            actions.append({"action": "skip", "reason": "do_not_call", **ids})
            continue
        if not verdict["allowed"]:
            actions.append(_blocked(verdict, now, **ids))
            continue
        if record_attempts:
            store.add_attempt(cid, now, kind="followup")
        store.set_callback_status(cb["id"], "done")
        planned.add(cid)
        actions.append({"action": "call", "kind": "callback", **ids})

    return actions