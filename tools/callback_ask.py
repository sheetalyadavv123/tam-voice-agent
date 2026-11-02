"""The answers to "what day and time should we call?", read by the code.

Every callback flow uses this (someone else answered, a relative who can't commit,
hardship, a customer who asks to be called later). spoken.callback_time reads one
answer; this adds the day-only rule:

- A day with no time ("kal", "Monday", "parson") gets one question for the time.
- If the next answer still has no time ("pata nahi kab", "koi bhi time"), the call is
  booked on that day at DEFAULT_CALLBACK_HOUR, and the close names the day only.
- A time given for that day ("shaam 5 baje") is booked on that day, not today.
- A day outside the allowed days, or too far, or a day with a part of the day but no
  hour ("kal shaam"), is not day-only: the flow's usual handling applies.
"""
from datetime import datetime

from tools import policy, rules, spoken
from tools.dates import dates_said, days_named, say


def day_named(text, today):
    """The day the caller named, or None. A date ("5 November") wins over a weekday."""
    said = dates_said(text, today)
    if said:
        return said[-1]
    days = days_named(text, today)
    return days[-1][1] if days else None


def at_default_hour(day, tz):
    return datetime(day.year, day.month, day.day, policy.DEFAULT_CALLBACK_HOUR, 0, tzinfo=tz)


say_day = say  # "Monday 9 November", in Hindi lines too, as say_when writes days


def book(call, said, reason):
    """Book what read() returned under "when": the caller's time, or their day at the
    default hour. Returns schedule_callback's result."""
    w = said["when"]
    if said.get("day_only"):
        return call.schedule_day_only(w.day, w.month, w.year, reason)
    return call.schedule_callback(w.day, w.month, w.year, w.hour, w.minute, reason)


class TimeAsk:
    def __init__(self, clock):
        self.clock = clock
        self.day = None  # a day the caller gave without a time, once asked for the time

    @property
    def waiting(self):
        """Asked for the time on a day: the next answer books that day either way."""
        return self.day is not None

    def _day_only_ok(self, day, now):
        return rules.validate_callback_time(at_default_hour(day, now.tzinfo), now)["ok"]

    def read(self, text):
        """One answer. Returns
          {"when": datetime}                    a time to book (the caller's)
          {"when": datetime, "day_only": True}  their day at DEFAULT_CALLBACK_HOUR
          {"ask_time": date}                    ask once for the time on that day
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
        part_of_day = any(p.search(text) for _, p in spoken.PART_OF_DAY)
        if part_of_day or (day is not None and not self._day_only_ok(day, now)):
            self.day = None  # "kal shaam", "agle mahine": not day-only, the usual handling
            return said
        if day is not None:
            if self.day is None:  # first time: ask once for the time
                self.day = day
                return {"ask_time": day}
            self.day = day  # asked already; "nahi, Tuesday" moves the day
        if self.day is not None:  # asked once, still no time: the default hour
            day, self.day = self.day, None
            return {"when": at_default_hour(day, now.tzinfo), "day_only": True}
        return said
