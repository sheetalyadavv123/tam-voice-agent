"""Acts out the collection story with no AI, no voice and no credits."""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from tools import simulator
from tools.collection_tools import Call
from tools.followups import run_due_followups
from tools.rules import IST
from tools.store import MemoryStore


class Clock:
    def __init__(self, at):
        self.at = at

    def __call__(self):
        return self.at


def ist(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=IST)


clock = Clock(ist(27, 10))
store = MemoryStore.from_files(clock=clock)


def at(when, title):
    clock.at = when
    print(f"\n=== {when:%d %b %H:%M} IST: {title}")


def scheduler():
    for a in run_due_followups(store, clock()):
        extra = a.get("kind") or a.get("reason") or ""
        print(f"  scheduler -> {a['action']:<14} {a['customer_id']}  {extra} {a.get('when', '')}")


def new_call(cid):
    c = Call(store, cid, session_id=f"{cid}-{clock():%d%H%M}", clock=clock)
    c.report_person("customer")
    return c


at(ist(27, 10), "morning scheduler")
scheduler()
c = new_call("C011")
print("  C011 follow-up call. Details:", c.get_due_details())
print("  C011 promises a new date:", c.record_promise_to_pay(29, 10, 2026))
c.record_outcome("promise_to_pay")

store.add_attempt("C002", clock(), kind="reminder")
c = new_call("C002")
print("  C002 reminder call. Promise:", c.record_promise_to_pay(28, 10, 2026))
c.record_outcome("promise_to_pay")

at(ist(28, 11), "C002's promised day")
scheduler()
c = new_call("C002")
print("  C002 follow-up. Link:", c.send_payment_link("sms"))
print("  customer pays:", simulator.pay_link(store, "C002"))
print("  payment check:", c.check_payment_status())
c.record_outcome("paid_now")

at(ist(28, 15), "afternoon scheduler")
scheduler()

at(ist(29, 20, 30), "evening of C011's promised day")
scheduler()

at(ist(30, 9), "grace day")
scheduler()

at(ist(31, 9), "after the grace day")
scheduler()

print("\nFinal promise states:")
for cid in ("C002", "C003", "C011"):
    for p in store.promises_for(cid):
        print(f"  {cid}  {p['promised_date']}  {p['status']}")