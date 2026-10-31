"""The state of one collection call, and the tools the AI may use during it."""
import logging
from datetime import datetime

from tools import caller_words, dates, policy, rules
from tools.rules import IST
from tools.store import CHANNELS
from typing import Literal

logger = logging.getLogger("tam-voice-agent")

PERSON_STATUSES = {"customer", "third_party", "unavailable"}
CALLBACK_REASONS = {"customer_unavailable", "customer_requested", "human_followup", "payment_help"}
OUTCOMES = {
    "paid_now", "promise_to_pay", "already_paid_confirmed", "already_paid_unconfirmed",
    "dispute", "hardship", "refused", "third_party", "wrong_number", "not_available",
    "callback_requested", "human_callback", "no_resolution",
}

WEAKER_OUTCOMES = {"no_resolution", "refused", "callback_requested", "not_available"}

HANDOFF_REASONS = {
    "legal_question", "hardship", "dispute", "customer_asked_for_person", "customer_upset",
    "refused_to_pay", "promise_date_too_far", "payment_not_found", "partial_payment",
    "info_not_available", "tool_error", "identity_check_failed",
}
# After a promise the payment push is over; only these still reach a colleague.
HANDOFF_AFTER_PROMISE = {"legal_question", "hardship", "dispute", "customer_asked_for_person"}
# After one of these the payment push is over: a colleague takes it from here.
STOP_PUSH = {"hardship", "dispute"}

PersonStatus = Literal["customer", "third_party", "unavailable"]
Channel = Literal["sms", "whatsapp"]
AiCallbackReason = Literal["customer_unavailable", "customer_requested"]
Outcome = Literal[
    "paid_now", "promise_to_pay", "already_paid_confirmed", "already_paid_unconfirmed",
    "dispute", "hardship", "refused", "third_party", "wrong_number", "not_available",
    "callback_requested", "human_callback", "no_resolution",
]
HandoffReason = Literal[
    "legal_question", "hardship", "dispute", "customer_asked_for_person", "customer_upset",
    "refused_to_pay", "promise_date_too_far", "payment_not_found", "partial_payment",
    "info_not_available", "tool_error", "identity_check_failed",
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
        self.was_third_party = False    # ever said they are not the customer
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
        self.promise = None  # {"date": date, "amount": int} once one is saved
        self.links_before_promise = None  # after a promise, only one more link may go out
        self.pending_checks = 0
        self.callback_id = None
        self.handoff_callback_id = None
        self.caller_lines = []  # what the caller said, newest last
        # A date the agent read back ("6 November, सही?") and the caller said yes to. Until a
        # promise is saved, no link goes out; the link they asked for waits here.
        self.read_back_date = None
        self.link_waiting = False
        # "hardship" or "dispute" once the customer reported one: no more asking for payment
        self.push_stopped = None
        self.callback_confirmed = None  # the time the colleague will call, once agreed

    def heard(self, text):
        """The agent passes every finished caller message here."""
        if text and text.strip():
            self.caller_lines.append(text.strip())

    def caller_agreed_to(self, agent_text, caller_text):
        """The agent's last message and the caller's answer. If the agent read back a date
        from today on and the caller said yes, remember it. Returns that date or None."""
        today = self.clock().date()
        future = [d for d in dates.dates_said(agent_text or "", today) if d >= today]
        if not future or not caller_words.agrees(caller_text or ""):
            return None
        if self.promise and self.promise["date"] == future[-1]:
            return None  # already the saved promise
        self.read_back_date = future[-1]
        return self.read_back_date

    # ---- facts the code (not the AI) may use ----
    @property
    def first_name(self):
        return self._customer["name"].split()[0]

    @property
    def language(self):
        return self._customer.get("language", "hi")

    @property
    def link_sent_after_promise(self):
        return (self.links_before_promise is not None
                and self.store.links_sent_in_session(self.session_id) > self.links_before_promise)

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
        if status == self.person_status == "customer" and self.unlocked:
            return {"ok": True, "duplicate": True, "next": "continue_the_conversation"}
        # Someone who said they are not the customer stays that way: they can't become
        # "unavailable", and becoming the customer always needs the date of birth.
        if self.was_third_party and status == "unavailable":
            return {"ok": False, "reason": "already_third_party",
                    "next": "ask_when_to_reach_the_customer"}
        if self.was_third_party and status == "customer" and self.person_status != "customer":
            if self.person_corrections >= 1:
                return {"ok": False, "reason": "already_third_party"}
            if not caller_words.says_is_the_customer(self.caller_lines, self.first_name):
                return {"ok": False, "reason": "caller_did_not_say_they_are_the_customer",
                        "next": "ask_when_to_reach_the_customer"}
            self.person_corrections += 1
            self.needs_dob = True
        elif status == "customer" and self.person_status != "customer":
            # A bare "okay" or "kaun?" is not "yes, it's me"
            last = self.caller_lines[-1] if self.caller_lines else ""
            if not caller_words.confirms_identity(last, self.first_name):
                logger.info("RULE identity_not_confirmed: %r", last[:60])
                return {"ok": False, "reason": "caller_did_not_confirm_identity",
                        "next": "confirm_identity_again"}
        if status == "third_party":
            self.was_third_party = True
        self.person_status = status
        if status == "customer":
            if self.unlocked:  # hand over the EMI facts now, so they can't be skipped
                return {"ok": True, "next": "give_reminder", "due": self.get_due_details()}
            return {"ok": True, "next": "ask_date_of_birth"}
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
        if not caller_words.said_a_date(self.caller_lines):  # a guess would waste a try
            return {"verified": False, "reason": "caller_did_not_say_a_date",
                    "next": "ask_for_date_of_birth"}
        real = self._real_date(day, month, year)
        if real is None:
            self.impossible_dates += 1
            if self._locked():
                return self._lock()
            return {"verified": False, "error": "impossible_date", "next": "ask_full_date_again"}
        self.dob_attempts += 1
        if real.isoformat() == self._customer["dob"]:
            self.dob_verified = True
            return {"verified": True, "next": "give_reminder", "due": self.get_due_details()}
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
        refused = self._push_refused()
        if refused:
            return {"sent": False, **refused}
        # Only send when the caller named a channel; "okay" or "haan" is not enough.
        named = caller_words.named_channel(self.caller_lines)
        if self.read_back_date:  # an agreed date that is not the saved promise (yet)
            self.link_waiting = named is not None
            logger.info("RULE record_promise_first: %s was agreed but not saved", self.read_back_date)
            return {"sent": False, "reason": "record_promise_first",
                    "date": self.read_back_date.isoformat(), "next": "record_promise_first"}
        if named is None:
            return {"sent": False, "reason": "caller_did_not_name_a_channel",
                    "next": "ask_sms_or_whatsapp"}
        sent = self.store.links_sent_in_session(self.session_id)
        if self.links_before_promise is not None and sent > self.links_before_promise:
            return {**self._already_sent(), "reason": "already_sent_after_promise",
                    "next": "say_the_link_was_already_sent_on_that_channel_and_close"}
        if sent >= policy.MAX_LINK_SENDS_PER_CALL:
            return {**self._already_sent(), "reason": "limit_reached",
                    "next": "offer_promise_or_callback"}
        link = self.store.add_payment_link(
            self.customer_id, loan["emi_amount"], named, self.session_id)
        nxt = "ask_caller_to_pay_then_tell_you"
        if self.promise_recorded:
            nxt = "say_link_sent_remind_date_and_close"
            logger.info("RULE promise_ends_push: link sent after a promise, closing")
        return {"sent": True, "channel": named, "to_number_ending": self._number_ending(),
                "amount": link["amount"], "next": nxt}

    def _number_ending(self):
        digits = "".join(ch for ch in self._customer["phone"] if ch.isdigit())
        return digits[-4:]

    def _already_sent(self):
        """A refusal that says where the earlier link went, so the AI can't invent it."""
        link = self.store.latest_link(self.customer_id)
        return {"sent": False, "already_sent_on": link["channel"],
                "to_number_ending": self._number_ending()}

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
            if self.promise_recorded:
                logger.info("RULE promise_ends_push: payment pending after a promise, closing")
                return {"status": "pending", "next": "remind_promise_date_and_close"}
            nxt = "offer_promise_or_callback" if self.pending_checks >= 2 else "ask_if_they_have_paid_yet"
            return {"status": "pending", "next": nxt}
        return {"status": "none", "next": "ask_when_they_can_pay"}

    def _push_refused(self):
        """After hardship or a dispute, payment only goes ahead if the customer offers it."""
        if not self.push_stopped or caller_words.offers_to_pay(self.caller_lines):
            return None
        return {"reason": "payment_push_stopped", "next": "do_not_ask_for_payment_ask_callback_time"}

    # ---- tool: promise to pay ----
    def record_promise_to_pay(self, day, month, year):
        if not self.unlocked:
            return {"error": "not_unlocked"}
        loan = self._customer["loan"]
        if loan["status"] == "closed" or loan["emi_amount"] <= 0:
            return {"saved": False, "reason": "no_payment_due"}
        refused = self._push_refused()
        if refused:
            return {"saved": False, **refused}
        check = rules.validate_promise_date(day, month, year, today=self.clock().date())
        self.read_back_date = None  # saved or not, the link no longer waits on it
        if not check["ok"]:
            self.link_waiting = False
            nxt = "offer_human_callback" if check["reason"] == "too_far" else "ask_date_again"
            return {"saved": False, "reason": check["reason"], "next": nxt}
        saved = self.store.add_promise(
            self.customer_id, check["date"], loan["emi_amount"], self.session_id)
        for other in self.store.promises_for(self.customer_id, "open"):
            if other["id"] != saved["id"]:
                self.store.set_promise_status(other["id"], "cancelled")
        self.promise_recorded = True
        self.promise = {"date": check["date"], "amount": saved["amount"]}
        if self.links_before_promise is None:
            self.links_before_promise = self.store.links_sent_in_session(self.session_id)
        d = check["date"]
        return {"saved": True, "date": d.isoformat(), "say_date": f"{d.day} {d:%B}",
                "amount": saved["amount"],
                "duplicate": saved["duplicate"],
                "next": "confirm_amount_and_date_then_offer_payment_link_once"}

    # ---- tool: schedule a callback ----
    def schedule_callback(self, day, month, year, hour, minute, reason):
        if self.person_status is None:
            return {"error": "confirm_person_first"}
        if reason not in CALLBACK_REASONS:
            return {"saved": False, "error": "invalid_reason", "allowed": sorted(CALLBACK_REASONS)}
        # The time must come from the caller, never from the AI.
        if not caller_words.named_a_time(self.caller_lines):
            return {"saved": False, "reason": "caller_did_not_give_a_time",
                    "next": "ask_the_caller_for_a_day_and_time"}
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
        if self.handoff_callback_id is not None:
            # transfer_to_human already booked a colleague: move that call, don't add another
            self.store.set_callback_due(self.handoff_callback_id, due)
            self.callback_confirmed = due
            logger.info("RULE one_callback_per_handoff: colleague callback moved to %s", due.isoformat())
            return {"saved": True, "when": due.isoformat(), "updated": "colleague_callback"}
        if self.callback_id is not None:  # a new time replaces the old one
            self.store.set_callback_status(self.callback_id, "cancelled")
        cb = self.store.add_callback(self.customer_id, due, reason, self.session_id)
        self.callback_id = cb["id"]
        self.callback_confirmed = due
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
            refused = {"saved": False, "error": "not_supported_by_records", "outcome": outcome}
            if outcome == "paid_now" and self.prior_payment_seen:
                refused["use"] = "already_paid_confirmed"
            return refused
        # the records win: a paid or promised call can't be logged as a lesser outcome
        better = ("paid_now" if self.payment_confirmed
                  else "promise_to_pay" if self.promise_recorded else None)
        if better and outcome in WEAKER_OUTCOMES:
            return {"saved": False, "error": "records_show_" + better, "use": better}
        if self.person_status == "third_party" and outcome in ("not_available", "callback_requested"):
            return {"saved": False, "error": "someone_else_answered", "use": "third_party"}
        self.outcome = outcome
        if outcome in STOP_PUSH:
            self._stop_push(outcome)
        self.note = (note or "")[:200]
        return {"saved": True}

    def finalize_outcome(self):
        """Run when the call ends. If the AI never recorded an outcome, pick one from
        what the records show. Returns True if it had to fill one in."""
        if self.outcome is not None:
            return False
        if self.payment_confirmed:
            self.outcome = "paid_now"
        elif self.promise_recorded:
            self.outcome = "promise_to_pay"
        elif self.handoff_requested:
            self.outcome = "human_callback"
        elif self.prior_payment_seen:
            self.outcome = "already_paid_confirmed"
        elif self.person_status == "third_party":
            self.outcome = "third_party"
        elif self.person_status == "unavailable":
            self.outcome = "not_available"
        elif self.callback_id is not None:
            self.outcome = "callback_requested"
        else:
            self.outcome = "no_resolution"
        self.note = (self.note + " [outcome filled in by code]").strip()[:200]
        return True

    def record_outcome_or_better(self, outcome, note=""):
        """record_outcome for code paths: if the records show a better label, use it."""
        result = self.record_outcome(outcome, note)
        if not result.get("saved") and result.get("use"):
            result = self.record_outcome(result["use"], note)
        return result

    # ---- tool: ask for a human (a callback until real transfers exist) ----
    def transfer_to_human(self, reason):
        if reason not in HANDOFF_REASONS:
            return {"requested": False, "error": "invalid_reason", "allowed": sorted(HANDOFF_REASONS)}
        if self.person_status == "third_party":  # a colleague calls the customer, not them
            return {"requested": False, "reason": "not_the_customer",
                    "next": "say_we_will_try_the_customer_again_later"}
        if reason == "customer_asked_for_person" and not caller_words.asks_for_person(self.caller_lines):
            return {"requested": False, "reason": "caller_did_not_ask_for_a_person",
                    "next": "continue_the_conversation"}
        if self.promise_recorded and reason not in HANDOFF_AFTER_PROMISE:
            return {"requested": False, "reason": "promise_already_recorded",
                    "next": "remind_promise_date_and_close"}
        self.handoff_requested = True
        if self.handoff_callback_id is None:
            due = rules.next_window_start(self.clock())
            cb = self.store.add_callback(self.customer_id, due, "human_followup", self.session_id)
            self.handoff_callback_id = cb["id"]
        if self.outcome is None:
            self.outcome = "human_callback"
        if not self.note:
            self.note = (reason or "")[:200]
        if reason in STOP_PUSH:
            self._stop_push(reason)
            return {"requested": True,
                    "next": "say_a_colleague_will_call_and_ask_what_day_and_time_suits_them"}
        return {"requested": True,
                "next": "tell_caller_a_colleague_will_call_back_do_not_say_connecting_now"}

    def _stop_push(self, reason):
        if self.push_stopped is None:
            logger.info("RULE payment_push_stopped: %s, no more asking for payment", reason)
        self.push_stopped = self.push_stopped or reason