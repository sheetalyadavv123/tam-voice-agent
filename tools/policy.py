import os

from dotenv import load_dotenv

load_dotenv()

# A clear "yes, speaking" from the customer unlocks the EMI reminder. Only someone who
# first said they were someone else must pass the date-of-birth check.

# When someone else answers: "none" shares nothing with them; "dob_verified" may talk to
# them about the account once they agree and give the customer's date of birth.
THIRD_PARTY_DISCLOSURE = os.getenv("THIRD_PARTY_DISCLOSURE") or "dob_verified"
if THIRD_PARTY_DISCLOSURE not in ("none", "dob_verified"):
    raise ValueError("THIRD_PARTY_DISCLOSURE must be none or dob_verified, "
                     f"got {THIRD_PARTY_DISCLOSURE}")

# Calling window in IST (24-hour clock). End hour is exclusive: 19 means before 7 pm.
CALL_WINDOW_START = int(os.getenv("CALL_WINDOW_START", "8"))
CALL_WINDOW_END = int(os.getenv("CALL_WINDOW_END", "19"))

# The caller named a day for a callback but no time, even when asked once: the call is
# booked at this hour on that day (with the note "caller gave day only").
DEFAULT_CALLBACK_HOUR = int(os.getenv("DEFAULT_CALLBACK_HOUR") or "11")
if not CALL_WINDOW_START <= DEFAULT_CALLBACK_HOUR < CALL_WINDOW_END:
    raise ValueError(f"DEFAULT_CALLBACK_HOUR must be inside the calling window "
                     f"({CALL_WINDOW_START} to {CALL_WINDOW_END - 1}), got {DEFAULT_CALLBACK_HOUR}")

MAX_ATTEMPTS_PER_DAY = int(os.getenv("MAX_ATTEMPTS_PER_DAY", "2"))
MAX_ATTEMPTS_PER_WEEK = int(os.getenv("MAX_ATTEMPTS_PER_WEEK", "5"))
MAX_PROMISE_DAYS = int(os.getenv("MAX_PROMISE_DAYS", "7"))
MAX_LINK_SENDS_PER_CALL = int(os.getenv("MAX_LINK_SENDS_PER_CALL", "2"))

MAX_DOB_ATTEMPTS = int(os.getenv("MAX_DOB_ATTEMPTS", "3"))
MAX_IMPOSSIBLE_DATES = int(os.getenv("MAX_IMPOSSIBLE_DATES", "3"))
PROMISE_GRACE_DAYS = int(os.getenv("PROMISE_GRACE_DAYS", "1"))