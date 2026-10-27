"""Where collection data lives. Tools talk to a Store, never to files or the database.

MemoryStore keeps everything in memory (tests, local runs; nothing is written to disk).
A PostgresStore with the same method names comes later.
"""
import copy
import json
from datetime import date
from pathlib import Path

from tools.rules import now_ist

DATA = Path(__file__).parent.parent / "data"
LINK_BASE_URL = "https://pay.example.test/l/"  # .test is reserved, so it never resolves
CHANNELS = {"sms", "whatsapp"}
PROMISE_STATUSES = {"open", "kept", "broken", "cancelled"}
CALLBACK_STATUSES = {"open", "done", "cancelled"}


class MemoryStore:
    def __init__(self, customers, seed, clock=now_ist):
        self._clock = clock  # tests pass a fixed clock
        self._customers = {c["id"]: c for c in copy.deepcopy(customers)}
        self._defaults = seed.get("defaults", {})
        self._overrides = seed.get("overrides", {})
        self._payments = copy.deepcopy(seed.get("payments_feed", []))
        self._attempts = []
        self._promises = []
        self._links = []
        self._callbacks = []
        for p in seed.get("open_promises", []):
            self.add_promise(p["customer_id"], date.fromisoformat(p["promised_date"]), p["amount"])

    @classmethod
    def from_files(cls, clock=now_ist):
        customers = json.loads((DATA / "mock_db.json").read_text(encoding="utf-8"))["customers"]
        seed = json.loads((DATA / "collections_seed.json").read_text(encoding="utf-8"))
        return cls(customers, seed, clock)

    # ---- customers and the company's payment records (read-only) ----
    def get_customer(self, customer_id):
        c = self._customers.get(customer_id)
        if c is None:
            return None
        merged = {**self._defaults, **c, **self._overrides.get(customer_id, {})}
        return copy.deepcopy(merged)

    def customer_ids(self):
        return list(self._customers)

    def payments_for(self, customer_id):
        return [dict(p) for p in self._payments if p["customer_id"] == customer_id]

    # ---- call attempts ----
    def add_attempt(self, customer_id, at, session_id=None, kind="reminder", outcome=None):
        self._attempts.append({"customer_id": customer_id, "at": at, "session_id": session_id,
                               "kind": kind, "outcome": outcome})

    def attempt_times(self, customer_id):
        return [a["at"] for a in self._attempts if a["customer_id"] == customer_id]

    # ---- promises to pay ----
    def add_promise(self, customer_id, promised_date, amount, session_id=None):
        """Saving the same open promise twice returns the first one (duplicate=True)."""
        for p in self._promises:
            if (p["customer_id"] == customer_id and p["promised_date"] == promised_date
                    and p["status"] == "open"):
                return {**p, "duplicate": True}
        p = {"id": len(self._promises) + 1, "customer_id": customer_id,
             "promised_date": promised_date, "amount": amount, "status": "open",
             "session_id": session_id, "created_at": self._clock()}
        self._promises.append(p)
        return {**p, "duplicate": False}

    def promises_for(self, customer_id, status=None):
        return [dict(p) for p in self._promises
                if p["customer_id"] == customer_id and (status is None or p["status"] == status)]

    def promises_due_on(self, day, status="open"):
        return [dict(p) for p in self._promises
                if p["promised_date"] == day and p["status"] == status]

    def set_promise_status(self, promise_id, status):
        if status not in PROMISE_STATUSES:
            raise ValueError(f"bad promise status: {status}")
        for p in self._promises:
            if p["id"] == promise_id:
                p["status"] = status
                return dict(p)
        return None

    # ---- payment links ----
    def add_payment_link(self, customer_id, amount, channel, session_id=None):
        if channel not in CHANNELS:
            raise ValueError(f"bad channel: {channel}")
        link_id = f"PL{len(self._links) + 1:04d}"
        link = {"id": link_id, "customer_id": customer_id, "amount": amount,
                "url": LINK_BASE_URL + link_id, "channel": channel, "status": "sent",
                "session_id": session_id, "created_at": self._clock(), "paid_at": None}
        self._links.append(link)
        return dict(link)

    def links_sent_in_session(self, session_id):
        return sum(1 for l in self._links if l["session_id"] == session_id)

    def latest_link(self, customer_id):
        mine = [l for l in self._links if l["customer_id"] == customer_id]
        return dict(mine[-1]) if mine else None

    def mark_link_paid(self, link_id):
        """Used by the payment simulator. Stands in for the payment gateway's confirmation."""
        for l in self._links:
            if l["id"] == link_id:
                l["status"] = "paid"
                l["paid_at"] = self._clock()
                return dict(l)
        return None

    # ---- callbacks ----
    def add_callback(self, customer_id, due_at, reason, session_id=None):
        cb = {"id": len(self._callbacks) + 1, "customer_id": customer_id, "due_at": due_at,
              "reason": reason, "status": "open", "session_id": session_id,
              "created_at": self._clock()}
        self._callbacks.append(cb)
        return dict(cb)

    def callbacks_due(self, now):
        return [dict(c) for c in self._callbacks if c["status"] == "open" and c["due_at"] <= now]

    def set_callback_status(self, callback_id, status):
        if status not in CALLBACK_STATUSES:
            raise ValueError(f"bad callback status: {status}")
        for c in self._callbacks:
            if c["id"] == callback_id:
                c["status"] = status
                return dict(c)
        return None