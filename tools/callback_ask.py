"""The answers to "what day and time should we call?", read by the code.

Every callback flow uses this (someone else answered, a relative who can't commit,
hardship, a customer who asks to be called later). spoken.callback_time reads one
answer; this adds:

- A day is remembered from any answer ("call me on Monday" ... "whenever").
- "Any time" ("anytime", "whenever", "koi bhi time", "किसी भी time", "kabhi bhi",
  "jab chahe") is an answer: that day at DEFAULT_CALLBACK_HOUR straight away, with the
  note "caller said any time". With no day yet, the flow asks for the day as usual.
- A day with no time gets one question for the time. The time is asked at most once in
  a flow (the flow's own "please tell me the time" counts too: mark_asked): after that,
  any answer without a time books the day at DEFAULT_CALLBACK_HOUR, note "caller gave
  day only". The close names the day only.
- A time given later ("shaam 5 baje") is booked on the day they gave, not today.
- A day outside the allowed days or too far is not remembered: the usual handling.
"""
import re
from datetime import datetime, timedelta

from tools import policy, rules, spoken
from tools.collection_tools import ANY_TIME_NOTE, DAY_ONLY_NOTE
from tools.dates import dates_said, days_named, say

ANY_TIME = re.compile(
    r"\b(?:any\s*time|anytime|whenever|any\s+time\s+is\s+(?:fine|ok|okay)"
    r"|(?:koi|kisi)\s+bhi\s+(?:time|samay|waqt|vakt)|kabhi\s+bhi|jab\s+(?:chahe|chaho|chahiye|marzi|mann)"
    r"|jab\s+bhi)\b"
    r"|(?:कोई|किसी)\s+भी\s+(?:time|टाइम|समय|वक्त|वक़्त)|कभी\s+भी|जब\s+(?:चाहे|चाहो|मर्ज़ी|मर्जी|मन)|जब\s+भी",
    re.I)


def says_any_time(text):
    return bool(ANY_TIME.search(text or ""))


def day_named(text, today):
    """The day the caller named, or None. A date ("5 November") wins over a weekday."""
    said = dates_said(text, today)
    if said:
        return said[-1]
    days = days_named(text, today)
    return days[-1][1] if days else None


def at_default_hour(day, tz):
    return datetime(day.year, day.month, day.day, policy.DEFAULT_CALLBACK_HOUR, 0, tzinfo=tz)


def next_default_slot(now):
    """The first DEFAULT_CALLBACK_HOUR still ahead and inside the rules: today's if it
    hasn't passed, else tomorrow's."""
    for days in range(3):
        when = at_default_hour(now.date() + timedelta(days=days), now.tzinfo)
        if rules.validate_callback_time(when, now)["ok"]:
            return when
    return None


say_day = say  # "Monday 9 November", in Hindi lines too, as say_when writes days


def book(call, said, reason):
    """Book what read() returned under "when": the caller's time, or their day at the
    default hour (with its note). Returns schedule_callback's result."""
    w = said["when"]
    if said.get("day_only"):
        return call.schedule_day_only(w.day, w.month, w.year, reason, note=said["note"])
    return call.schedule_callback(w.day, w.month, w.year, w.hour, w.minute, reason)


class TimeAsk:
    def __init__(self, clock):
        self.clock = clock
        self.day = None  # a day the caller gave, still without a time
        self.asked = False  # the time was asked for once: never again

    @property
    def waiting(self):
        """A day is known: an answer without a time ends in a booking, not a close."""
        return self.day is not None

    def mark_asked(self):
        """The flow asked for the time itself ("please tell me the day and time")."""
        self.asked = True

    def _ok(self, day, now):
        return rules.validate_callback_time(at_default_hour(day, now.tzinfo), now)["ok"]

    def _default(self, day, now, note):
        self.day = None
        return {"when": at_default_hour(day, now.tzinfo), "day_only": True, "note": note}

    def read(self, text):
        """One answer. Returns
          {"when": datetime}                                   the caller's time
          {"when": datetime, "day_only": True, "note": ...}    a day at DEFAULT_CALLBACK_HOUR
          {"ask_time": date}                                   ask (once) for the time
          otherwise what spoken.callback_time returned ({"missing": "time" or "both"})."""
        now = self.clock()
        said = spoken.callback_time(text, now)
        day = day_named(text, now.date())
        if "when" in said:
            w = said["when"]
            if self.day is not None and day is None:  # "shaam 5 baje" for the day they gave
                w = w.replace(year=self.day.year, month=self.day.month, day=self.day.day)
            self.day = None
            return {"when": w}
        if day is not None:
            if not self._ok(day, now):
                return said  # "aaj" after the default hour, too far: the usual handling
            self.day = day  # "nahi, Tuesday" moves the day
        if self.day is None:
            return said  # "whenever" with no day yet: the flow asks for the day
        if says_any_time(text):
            return self._default(self.day, now, ANY_TIME_NOTE)
        if self.asked:  # asked once already: no more questions
            return self._default(self.day, now, DAY_ONLY_NOTE)
        self.asked = True
        return {"ask_time": self.day}
