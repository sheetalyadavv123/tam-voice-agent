"""A pay date beyond MAX_PROMISE_DAYS ("दस दिन बाद pay कर दूंगी"), run by the code.

    far date -> "ठीक है, 12 November नोट किया। क्या आप 9 November तक pay कर पाएंगे?"
        a date within the limit, or a plain yes (= the last allowed day) -> the promise is
            recorded as usual and the AI carries on (amount, date, payment link)
        anything else (the same or another far date, a no, "nahi ho payega") ->
            "ठीक है, 12 November के लिए हमारी एक सहयोगी आपसे बात करके confirm करेंगी।
             उन्हें आपको किस दिन और किस समय कॉल करना ठीक रहेगा?"
            -> one answer, then the colleague's callback is booked and the call closes:
               their time, or their day at DEFAULT_CALLBACK_HOUR, or (nothing usable) the
               next DEFAULT_CALLBACK_HOUR. Outcome human_callback, note "customer offered
               12 November, beyond limit".

The earlier date is asked for once; the time for the callback is asked for once.
"""
import logging
import re
from datetime import timedelta

from tools import caller_words, policy
from tools.callback_ask import TimeAsk, at_default_hour, next_default_slot, say_day
from tools.collection_tools import DAY_ONLY_NOTE, NO_TIME_NOTE
from tools.dates import say_when
from tools.relative_dates import last_pay_date
from tools.third_party import Reply

logger = logging.getLogger("tam-voice-agent")

# "9 तक नहीं हो पाएगा", "that's not possible": a no, even with a date in it
CANT_BY = re.compile(
    r"\b(?:nahi|nahin|nhi)\s+(?:ho|kar|de|bhar)\s+pa|\bpossible\s+nahi|\bmushkil|\bmumkin\s+nahi"
    r"|\bcan'?t\b|\bcannot\b|\bnot\s+possible\b|\bwon'?t\s+be\s+able|\bnot\s+able\b"
    r"|नहीं\s+(?:हो|कर|दे|भर)\s+पा|मुश्किल|मुमकिन\s+नहीं|संभव\s+नहीं|possible\s+नहीं",
    re.I)


def say_date(d):
    """"12 November", as the promise lines say dates."""
    return f"{d.day} {d:%B}"


class FarDateFlow:
    def __init__(self, call):
        self.call = call
        self.state = None  # "asked" (an earlier date), "time" (the colleague's call), "done"
        self.offered = None  # the far date the caller gave
        self.ask = TimeAsk(call.clock)

    @property
    def active(self):
        return self.state in ("asked", "time")

    def latest(self):
        return self.call.clock().date() + timedelta(days=policy.MAX_PROMISE_DAYS)

    def far_date(self, text):
        """The pay date the caller named, if it is beyond the limit; else None."""
        d = last_pay_date(text, self.call.clock().date())
        return d if d is not None and d > self.latest() else None

    def _line(self, lines, lang, **values):
        return caller_words.line(lines, lang, name=self.call.first_name, **values)

    def _tp(self):
        return self.call.person_status == "third_party"

    def start(self, offered, lang):
        """Ask once for a date within the limit."""
        self.state, self.offered = "asked", offered
        logger.info("RULE promise_date_far: %s is beyond %d days, asking once for %s or earlier",
                    offered, policy.MAX_PROMISE_DAYS, self.latest())
        lines = caller_words.FAR_DATE_EARLIER_TP if self._tp() else caller_words.FAR_DATE_EARLIER
        return Reply(self._line(lines, lang, date=say_date(offered), latest=say_date(self.latest())))

    def answer(self, text, lang):
        """The answer to "can you pay by <latest>?". Returns ("promise", date) to record,
        or ("colleague", Reply) with the line that asks when the colleague may call."""
        today = self.call.clock().date()
        d = last_pay_date(text, today)
        if not CANT_BY.search(text):
            if d is not None and today <= d <= self.latest():
                self.state = "done"
                logger.info("RULE promise_date_far_earlier: %s accepted", d)
                return "promise", d
            if d is None and caller_words.agrees(text):  # "haan, kar dungi": by <latest>
                self.state = "done"
                logger.info("RULE promise_date_far_earlier: yes, %s", self.latest())
                return "promise", self.latest()
        if d is not None and d > self.latest():
            self.offered = d  # "nahi, 15 ko": the colleague hears the latest offer
        self.state = "time"
        self.ask.mark_asked()  # this line asks for the time; no second question
        logger.info("RULE promise_date_far_colleague: %r, %s still beyond the limit",
                    text[:60], self.offered)
        lines = caller_words.FAR_DATE_COLLEAGUE_TP if self._tp() else caller_words.FAR_DATE_COLLEAGUE
        return "colleague", Reply(self._line(lines, lang, date=say_date(self.offered)))

    def offered_note(self):
        who = self.call.promised_by or "customer"
        return f"{who} offered {say_date(self.offered)}, beyond limit"

    def hear_time(self, text, lang):
        """One answer for the colleague's call: book it and close, whatever it says."""
        call, now = self.call, self.call.clock()
        said = self.ask.read(text)
        attempts = []
        if "when" in said:
            attempts.append((said["when"], said.get("note") if said.get("day_only") else None))
            if not said.get("day_only"):  # outside the hours: that day at the default hour
                attempts.append((at_default_hour(said["when"].date(), now.tzinfo), DAY_ONLY_NOTE))
        attempts.append((next_default_slot(now), NO_TIME_NOTE))
        for when, day_only in attempts:
            if when is None:
                continue
            result = call.far_date_handoff(when, self.offered_note(), day_only)
            if result.get("saved"):
                self.state = "done"
                return self._close(lang, when, day_only)
        logger.warning("RULE promise_date_far_colleague: no callback could be booked")
        self.state = "done"
        call.handoff_requested = True
        call.record_outcome("human_callback", self.offered_note())
        return Reply(self._line(caller_words.HANDOFF_CLOSE_NO_TIME, lang), hang_up=True)

    def _close(self, lang, when, day_only):
        tp = self._tp()
        if day_only:
            lines = caller_words.FAR_DATE_CLOSE_DAY_TP if tp else caller_words.FAR_DATE_CLOSE_DAY
            return Reply(self._line(lines, lang, day=say_day(when)), hang_up=True)
        lines = caller_words.FAR_DATE_CLOSE_TP if tp else caller_words.FAR_DATE_CLOSE
        return Reply(self._line(lines, lang, when=say_when(when, lang)), hang_up=True)
