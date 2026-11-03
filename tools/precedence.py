"""Which rule takes a caller's message, once the call is unlocked. The order lives here and
nowhere else; agent/main.py (_paths) runs the path this returns. Pure: no AI, no I/O.

    legal -> wrong number -> hardship beyond the limit -> plain beyond the limit
          -> hardship within the limit -> normal promise -> hardship with no date

- Hardship counts if this message says it or the caller reported it earlier in the call.
- The date is the last day (or range) in this message; while the code is asking for an
  earlier date, a message with no date keeps the date already offered, so hardship said
  at any point in that flow takes the hardship-beyond-limit path.
- After a dispute or a closed hardship (the payment push is over), only legal and wrong
  number apply.
"""
from tools import caller_words

LEGAL = "legal"
WRONG_NUMBER = "wrong_number"
HARDSHIP_FAR = "hardship_far"
FAR = "far"
HARDSHIP_WITHIN = "hardship_within"
PROMISE = "promise"
HARDSHIP_NO_DATE = "hardship_no_date"
ORDER = (LEGAL, WRONG_NUMBER, HARDSHIP_FAR, FAR, HARDSHIP_WITHIN, PROMISE, HARDSHIP_NO_DATE)


def route(text, first_name, *, offer, latest, hardship_before=False, push_stopped=False):
    """The path for this message, or None (no rule here: the AI or a later check).

    offer: the relative_dates.Offer in play (or None); latest: the last allowed pay date;
    hardship_before: hardship was reported earlier in the call."""
    text = text or ""
    if caller_words.legal_question(text):
        return LEGAL
    if caller_words.wrong_number(text, first_name):
        return WRONG_NUMBER
    if push_stopped:
        return None
    hardship = hardship_before or caller_words.hardship(text)
    beyond = offer is not None and offer.last > latest
    if hardship and beyond:
        return HARDSHIP_FAR
    if beyond:
        return FAR
    if hardship and offer is not None:
        return HARDSHIP_WITHIN
    if offer is not None:
        return PROMISE
    if hardship:
        return HARDSHIP_NO_DATE
    return None
