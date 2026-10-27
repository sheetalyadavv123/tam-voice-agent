"""The state of one collection call, and the tools the AI may use during it."""
from tools import policy, rules

PERSON_STATUSES = {"customer", "third_party", "unavailable"}


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
        self.dob_attempts = 0
        self.impossible_dates = 0
        self.handoff_requested = False
        self.outcome = None
        self.note = ""

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
        return policy.VERIFICATION_MODE == "name_only" or self.dob_verified

    # ---- tool: who answered? ----
    def report_person(self, status):
        if status not in PERSON_STATUSES:
            return {"ok": False, "error": "invalid_status", "allowed": sorted(PERSON_STATUSES)}
        if self.person_status == "third_party" and status == "customer":
            return {"ok": False, "reason": "already_third_party"}
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
        if policy.VERIFICATION_MODE == "name_only":
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