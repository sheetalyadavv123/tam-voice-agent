"""The state of one collection call, and the tools the AI may use during it."""
from datetime import datetime

from tools import policy, rules
from tools.rules import IST
from tools.store import CHANNELS
from typing import Literal

PERSON_STATUSES = {"customer", "third_party", "unavailable"}
CALLBACK_REASONS = {"customer_unavailable", "customer_requested", "human_followup", "payment_help"}
OUTCOMES = {
    "paid_now", "promise_to_pay", "already_paid_confirmed", "already_paid_unconfirmed",
    "dispute", "hardship", "refused", "third_party", "wrong_number", "not_available",
    "callback_requested", "human_callback", "no_resolution",
}

PersonStatus = Literal["customer", "third_party", "unavailable"]
Channel = Literal["sms", "whatsapp"]
AiCallbackReason = Literal["customer_unavailable", "customer_requested"]
Outcome = Literal[
    "paid_now", "promise_to_pay", "already_paid_confirmed", "already_paid_unconfirmed",
    "dispute", "hardship", "refused", "third_party", "wrong_number", "not_available",
    "callback_requested", "human_callback", "no_resolution",
]


class Call:
    def __init__(self, store, customer_id, session_id=None, clock=rules.now_ist):
        self.store = store
        self.customer_id = customer_id
        self.session_id = session_id
        self.clock = clock
        self._customer = store.get_customer(customer_id)
        if self._customer is None:
            raise ValueError(f"unknown customer: {customer_id}")
        self.person_status = None       # customer | third_party | unavailable
        self.dob_verified = False
        self.person_corrections = 0
        self.needs_dob = False
        self.dob_attempts = 0
        self.impossible_dates = 0
        self.handoff_requested = False
        self.outcome = None
        self.note = ""
        # facts the records have confirmed during this call
        self.payment_confirmed = False
        self.prior_payment_seen = False
        self.promise_recorded = False
        self.pending_checks = 0
        self.callback_id = None
        self.handoff_callback_id = None

    # ---- facts the code (not the AI) may use ----
    @property
    def first_name(self):
        return self._customer["name"].split()[0]

    @property
    def language(self):
        return self._customer.get("language", "hi")

    @property
    def unlocked(self):
        """May we talk about the loan? Depends on VERIFICATION_MODE."""
        if self.person_status != "customer":
            return False
        return (policy.VERIFICATION_MODE == "name_only" and not self.needs_dob) or self.dob_verified

    def summary(self):
        return {
            "customer_id": self.customer_id,
            "person_status": self.person_status,
            "dob_verified": self.dob_verified,
            "outcome": self.outcome,
            "note": self.note,
            "handoff_requested": self.handoff_requested,
            "promise_recorded": self.promise_recorded,
            "payment_confirmed": self.payment_confirmed,
            "callback_scheduled": self.callback_id is not None,
        }

    # ---- tool: who answered? ----
    def report_person(self, status):
        if status not in PERSON_STATUSES:
            return {"ok": False, "error": "invalid_status", "allowed": sorted(PERSON_STATUSES)}
        if self.person_status == "third_party" and status == "customer":
            if self.person_corrections >= 1:
                return {"ok": False, "reason": "already_third_party"}
            self.person_corrections += 1
            self.needs_dob = True
        self.person_status = status
        if status == "customer":
            return {"ok": True, "next": "give_reminder" if self.unlocked else "ask_date_of_birth"}
        if status == "third_party":
            return {"ok": True, "next": "share_nothing_and_end_politely"}
        return {"ok": True, "next": "ask_when_to_call_back"}

    # ---- tool: date of birth (only used in name_plus_dob mode) ----
    def verify_identity(self, day, month, year):
        if self.person_status != "customer":
            return {"error": "confirm_person_first"}
        if policy.VERIFICATION_MODE == "name_only" and not self.needs_dob:
            return {"verified": True, "not_required": True}
        if self.dob_verified:
            return {"verified": True}
        if self._locked():
            return self._lock()
        real = self._real_date(day, month, year)
        if real is None:
            self.impossible_dates += 1
            if self._locked():
                return self._lock()
            return {"verified": False, "error": "impossible_date", "next": "ask_full_date_again"}
        self.dob_attempts += 1
        if real.isoformat() == self._customer["dob"]:
            self.dob_verified = True
            return {"verified": True}
        if self._locked():
            return self._lock()
        return {"verified": False, "attempts_left": policy.MAX_DOB_ATTEMPTS - self.dob_attempts}

    def _locked(self):
        return (self.dob_attempts >= policy.MAX_DOB_ATTEMPTS
                or self.impossible_dates >= policy.MAX_IMPOSSIBLE_DATES)

    def _lock(self):
        self.handoff_requested = True
        return {"verified": False, "locked": True, "next": "offer_human_callback"}

    def _real_date(self, day, month, year):
        d = rules.make_date(day, month, year)
        if d is None or not 1900 <= d.year <= self.clock().year:
            return None
        return d

    # ---- tool: the EMI facts ----
    def get_due_details(self):
        if not self.unlocked:
            return {"error": "not_unlocked"}
        loan = self._customer["loan"]
        if loan["status"] == "closed":
            return {"loan_closed": True, "name": self._customer["name"]}
        details = {
            "name": self._customer["name"],
            "loan_type": loan["type"],
            "emi_amount": loan["emi_amount"],
            "due_date": loan["due_date"],
            "overdue_days": loan["overdue_days"],
        }
        open_promises = self.store.promises_for(self.customer_id, "open")
        if open_promises:
            p = open_promises[0]
            details["open_promise"] = {"date": p["promised_date"].isoformat(), "amount": p["amount"]}
        return details

    # ---- tool: send a payment link ----
    def send_payment_link(self, channel):
        if not self.unlocked:
            return {"error": "not_unlocked"}
        if channel not in CHANNELS:
            return {"error": "invalid_channel", "allowed": sorted(CHANNELS)}
        loan = self._customer["loan"]
        if loan["status"] == "closed" or loan["emi_amount"] <= 0:
            return {"sent": False, "reason": "no_payment_due"}
        if self.store.links_sent_in_session(self.session_id) >= policy.MAX_LINK_SENDS_PER_CALL:
            return {"sent": False, "reason": "limit_reached", "next": "offer_promise_or_callback"}
        link = self.store.add_payment_link(
            self.customer_id, loan["emi_amount"], channel, self.session_id)
        digits = "".join(ch for ch in self._customer["phone"] if ch.isdigit())
        return {"sent": True, "channel": channel, "to_number_ending": digits[-4:],
                "amount": link["amount"], "next": "ask_caller_to_pay_then_tell_you"}

    # ---- tool: did the payment arrive? (records only, never the caller's word) ----
    def check_payment_status(self):
        if not self.unlocked:
            return {"error": "not_unlocked"}
        loan = self._customer["loan"]
        link = self.store.latest_link(self.customer_id)
        mine = link is not None and link["session_id"] == self.session_id
        if mine and link["status"] == "paid":
            self.payment_confirmed = True
            return {"status": "paid", "amount": link["amount"], "source": "payment_link",
                    "next": "thank_and_close"}
        for p in self.store.payments_for(self.customer_id):
            if p["received_on"] <= loan["last_payment_date"]:
                continue  # an older payment, not for this EMI
            self.prior_payment_seen = True
            if p["amount"] < loan["emi_amount"]:
                return {"status": "partial", "amount": p["amount"], "received_on": p["received_on"],
                        "source": "company_records", "next": "offer_human_callback"}
            if p["status"] == "posted":
                return {"status": "paid", "amount": p["amount"], "received_on": p["received_on"],
                        "source": "company_records", "next": "thank_and_close"}
            return {"status": "reconciling", "amount": p["amount"], "received_on": p["received_on"],
                    "source": "company_records", "next": "say_it_is_being_processed_and_close"}
        if mine:
            self.pending_checks += 1
            nxt = "offer_promise_or_callback" if self.pending_checks >= 2 else "ask_if_they_have_paid_yet"
            return {"status": "pending", "next": nxt}
        return {"status": "none", "next": "ask_when_they_can_pay"}

    # ---- tool: promise to pay ----
    def record_promise_to_pay(self, day, month, year):
        if not self.unlocked:
            return {"error": "not_unlocked"}
        loan = self._customer["loan"]
        if loan["status"] == "closed" or loan["emi_amount"] <= 0:
            return {"saved": False, "reason": "no_payment_due"}
        check = rules.validate_promise_date(day, month, year, today=self.clock().date())
        if not check["ok"]:
            nxt = "offer_human_callback" if check["reason"] == "too_far" else "ask_date_again"
            return {"saved": False, "reason": check["reason"], "next": nxt}
        saved = self.store.add_promise(
            self.customer_id, check["date"], loan["emi_amount"], self.session_id)
        for other in self.store.promises_for(self.customer_id, "open"):
            if other["id"] != saved["id"]:
                self.store.set_promise_status(other["id"], "cancelled")
        self.promise_recorded = True
        return {"saved": True, "date": check["date"].isoformat(), "amount": saved["amount"],
                "duplicate": saved["duplicate"],
                "next": "confirm_amount_and_date_then_offer_payment_link_once"}

    # ---- tool: schedule a callback ----
    def schedule_callback(self, day, month, year, hour, minute, reason):
        if self.person_status is None:
            return {"error": "confirm_person_first"}
        if reason not in CALLBACK_REASONS:
            return {"saved": False, "error": "invalid_reason", "allowed": sorted(CALLBACK_REASONS)}
        d = rules.make_date(day, month, year)
        try:
            due = datetime(d.year, d.month, d.day, int(hour), int(minute), tzinfo=IST) if d else None
        except (ValueError, TypeError):
            due = None
        if due is None:
            return {"saved": False, "reason": "impossible_time", "next": "ask_day_and_time_again"}
        check = rules.validate_callback_time(due, self.clock())
        if not check["ok"]:
            return {"saved": False, "reason": check["reason"], "next": "suggest_a_time_in_calling_hours"}
        if self.callback_id is not None:  # a new time replaces the old one
            self.store.set_callback_status(self.callback_id, "cancelled")
        cb = self.store.add_callback(self.customer_id, due, reason, self.session_id)
        self.callback_id = cb["id"]
        return {"saved": True, "when": due.isoformat()}

    # ---- tool: how did the call end? ----
    def record_outcome(self, outcome, note=""):
        if outcome not in OUTCOMES:
            return {"error": "invalid_outcome", "allowed": sorted(OUTCOMES)}
        needs_proof = {
            "paid_now": self.payment_confirmed,
            "promise_to_pay": self.promise_recorded,
            "already_paid_confirmed": self.prior_payment_seen,
        }
        if outcome in needs_proof and not needs_proof[outcome]:
            return {"saved": False, "error": "not_supported_by_records", "outcome": outcome}
        self.outcome = outcome
        self.note = (note or "")[:200]
        return {"saved": True}

    # ---- tool: ask for a human (a callback until real transfers exist) ----
    def transfer_to_human(self, reason):
        self.handoff_requested = True
        if self.handoff_callback_id is None:
            due = rules.next_window_start(self.clock())
            cb = self.store.add_callback(self.customer_id, due, "human_followup", self.session_id)
            self.handoff_callback_id = cb["id"]
        if self.outcome is None:
            self.outcome = "human_callback"
        if not self.note:
            self.note = (reason or "")[:200]
        return {"requested": True,
                "next": "tell_caller_a_colleague_will_call_back_do_not_say_connecting_now"}