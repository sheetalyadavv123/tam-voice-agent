import os

from dotenv import load_dotenv

load_dotenv()

# name_only: a "yes, speaking" unlocks the EMI reminder.
# name_plus_dob: the caller must also pass the date-of-birth check.
VERIFICATION_MODE = os.getenv("VERIFICATION_MODE", "name_only")

# Calling window in IST (24-hour clock). End hour is exclusive: 19 means before 7 pm.
CALL_WINDOW_START = int(os.getenv("CALL_WINDOW_START", "8"))
CALL_WINDOW_END = int(os.getenv("CALL_WINDOW_END", "19"))

MAX_ATTEMPTS_PER_DAY = int(os.getenv("MAX_ATTEMPTS_PER_DAY", "2"))
MAX_ATTEMPTS_PER_WEEK = int(os.getenv("MAX_ATTEMPTS_PER_WEEK", "5"))
MAX_PROMISE_DAYS = int(os.getenv("MAX_PROMISE_DAYS", "7"))
MAX_LINK_SENDS_PER_CALL = int(os.getenv("MAX_LINK_SENDS_PER_CALL", "2"))

MAX_DOB_ATTEMPTS = int(os.getenv("MAX_DOB_ATTEMPTS", "3"))
MAX_IMPOSSIBLE_DATES = int(os.getenv("MAX_IMPOSSIBLE_DATES", "3"))