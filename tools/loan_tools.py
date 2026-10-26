import json
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "mock_db.json"
MAX_ATTEMPTS = 3
OUTCOMES = {
    "paid_confirmed", "promise_to_pay", "dispute",
    "callback_requested", "wrong_person", "transferred", "no_resolution",
}


class Session:
    """Remembers what has happened during ONE call."""

    def __init__(self):
        self.customer_id = None
        self.verified = False
        self.attempts = 0
        self.outcome = None
        self.note = ""


def _customers():
    return json.loads(DB_PATH.read_text(encoding="utf-8"))["customers"]


def _last10(phone: str) -> str:
    return "".join(c for c in phone if c.isdigit())[-10:]


def _get(customer_id):
    return next((c for c in _customers() if c["id"] == customer_id), None)



def find_customer(session: Session, phone: str) -> dict:
    """Match by phone number. Reveals nothing about the customer yet."""
    for c in _customers():
        if _last10(c["phone"]) == _last10(phone):
            session.customer_id = c["id"]
            return {"found": True}
    return {"found": False}



def verify_identity(session: Session, dob: str) -> dict:
    """dob as YYYY-MM-DD. Locks after MAX_ATTEMPTS wrong tries."""
    if not session.customer_id:
        return {"verified": False, "error": "no_customer"}
    if session.attempts >= MAX_ATTEMPTS:
        return {"verified": False, "locked": True}
    session.attempts += 1
    if _get(session.customer_id)["dob"] == dob:
        session.verified = True
        return {"verified": True}
    return {"verified": False, "attempts_left": MAX_ATTEMPTS - session.attempts}



def get_loan_status(session: Session) -> dict:
    if not session.verified:
        return {"error": "not_verified"}
    c = _get(session.customer_id)
    return {"name": c["name"], **c["loan"]}



def record_outcome(session: Session, outcome: str, note: str = "") -> dict:
    if outcome not in OUTCOMES:
        return {"error": "invalid_outcome", "allowed": sorted(OUTCOMES)}
    session.outcome, session.note = outcome, note
    return {"saved": True}


def transfer_to_human(session: Session, reason: str) -> dict:
    session.outcome, session.note = "transferred", reason
    return {"transfer": True, "reason": reason}