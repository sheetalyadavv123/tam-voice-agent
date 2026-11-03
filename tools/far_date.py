"""A pay date beyond MAX_PROMISE_DAYS ("दस दिन बाद pay कर दूंगी"), run by the code.

    far date -> "ठीक है, 12 November नोट किया। क्या आप 9 November तक pay कर पाएंगे?"
        a date within the limit, or a plain yes (= the last allowed day) -> the promise is
            recorded as usual and the AI carries on (amount, date, payment link)
        the same or another far date, a no, "nahi ho payega" -> the close (below)
        an answer that can't be read (empty, garbled, another script) -> the same
            question once more; a second unclear answer -> the close

    the close: "ठीक है, 12 November थोड़ा आगे की तारीख है। इसके बारे में हमारी टीम से कोई
        आपसे बात करेगा। धन्यवाद, आपका दिन शुभ हो।" and the call ends. No day or time is asked:
        a human_followup callback is booked at the next DEFAULT_CALLBACK_HOUR inside the
        calling hours. Outcome human_callback, note "customer offered 12 November, beyond
        limit".
"""
import logging
import re
from datetime import timedelta

from tools import caller_words, policy
from tools.callback_ask import next_default_slot
from tools.relative_dates import Offer, last_offer, say_offer
from tools.third_party import Reply

logger = logging.getLogger("tam-voice-agent")

# "9 तक नहीं हो पाएगा", "that's not possible": a no, even with a date in it
CANT_BY = re.compile(
    r"\b(?:nahi|nahin|nhi)\s+(?:ho|kar|de|bhar)\s+pa|\bpossible\s+nahi|\bmushkil|\bmumkin\s+nahi"
    r"|\bcan'?t\b|\bcannot\b|\bnot\s+possible\b|\bwon'?t\s+be\s+able|\bnot\s+able\b"
    r"|नहीं\s+(?:हो|कर|दे|भर)\s+पा|मुश्किल|मुमकिन\s+नहीं|संभव\s+नहीं|possible\s+नहीं",
    re.I)
MAX_UNCLEAR = 1  # the earlier-date question is asked once more after an unclear answer


def say_date(d):
    """"12 November", as the promise lines say dates."""
    return f"{d.day} {d:%B}"


def latest_allowed(call):
    return call.clock().date() + timedelta(days=policy.MAX_PROMISE_DAYS)


def far_offer(call, text):
    """The day (or range: its last day counts) the caller named, if beyond the limit."""
    o = last_offer(text, call.clock().date())
    return o if o is not None and o.last > latest_allowed(call) else None


def book_followup(call, note, outcome):
    """The team's call at the next DEFAULT_CALLBACK_HOUR slot (the caller is not asked),
    with the note on the callback and the call."""
    when = next_default_slot(call.clock())
    result = call.far_date_handoff(when, note, outcome) if when else {"saved": False}
    if not result.get("saved"):
        logger.warning("RULE promise_date_far_close: no callback could be booked (%s)",
                       result.get("reason") or result.get("error"))
        call.handoff_requested = True
        call.record_outcome(outcome, note)
    return result


def hardship_close(call, offer, lang):
    """Hardship and a pay date beyond the limit: no push for an earlier date. A fixed kind
    line, the team's follow-up at the next default slot, outcome hardship, end the call."""
    note = f"hardship, offered {say_offer(offer, for_note=True)}, beyond limit"
    book_followup(call, note, "hardship")
    logger.info("RULE hardship_far_date: %s, no earlier-date question, ending the call", note)
    tp = call.person_status == "third_party"
    lines = caller_words.HARDSHIP_FAR_CLOSE_TP if tp else caller_words.HARDSHIP_FAR_CLOSE
    return Reply(caller_words.line(lines, lang, name=call.first_name, date=say_offer(offer, lang)),
                 hang_up=True)


def hardship_no_date_close(call, lang):
    """Hardship and no date, even after one question: a fixed kind line, the team's
    follow-up at the next default slot (no time question), outcome hardship, end the call."""
    note = "hardship, no pay date"
    book_followup(call, note, "hardship")
    logger.info("RULE hardship_no_date: %s, team follow-up, ending the call", note)
    tp = call.person_status == "third_party"
    lines = caller_words.HARDSHIP_NO_DATE_CLOSE_TP if tp else caller_words.HARDSHIP_NO_DATE_CLOSE
    return Reply(caller_words.line(lines, lang, name=call.first_name), hang_up=True)


class FarDateFlow:
    def __init__(self, call):
        self.call = call
        self.state = None  # "asked" (an earlier date), then "done"
        self.offered = None  # the far Offer the caller gave
        self.unclear = 0

    @property
    def active(self):
        return self.state == "asked"

    def latest(self):
        return latest_allowed(self.call)

    def far_date(self, text):
        """The Offer the caller named, if it is beyond the limit; else None."""
        return far_offer(self.call, text)

    def _line(self, lines, lang, **values):
        return caller_words.line(lines, lang, name=self.call.first_name, **values)

    def _tp(self):
        return self.call.person_status == "third_party"

    def _earlier_question(self, lang):
        lines = caller_words.FAR_DATE_EARLIER_TP if self._tp() else caller_words.FAR_DATE_EARLIER
        return Reply(self._line(lines, lang, date=say_offer(self.offered, lang),
                                latest=say_date(self.latest())))

    def start(self, offered, lang):
        """Ask once for a date within the limit. offered: an Offer, or a date."""
        if not isinstance(offered, Offer):
            offered = Offer("", offered, offered)
        self.state, self.offered = "asked", offered
        logger.info("RULE promise_date_far: %s is beyond %d days, asking once for %s or earlier",
                    say_offer(offered), policy.MAX_PROMISE_DAYS, self.latest())
        return self._earlier_question(lang)

    def answer(self, text, lang):
        """The answer to "can you pay by <latest>?". Returns ("promise", date) to record,
        ("ask", Reply) to ask the same question again, or ("close", Reply) to end the call."""
        text = text or ""
        today = self.call.clock().date()
        o = last_offer(text, today)
        d = o.last if o else None  # a range: its last day
        no = bool(CANT_BY.search(text))
        if not no and d is not None and today <= d <= self.latest():
            self.state = "done"
            logger.info("RULE promise_date_far_earlier: %s accepted", d)
            return "promise", d
        if d is not None and d > self.latest():
            self.offered = o  # "nahi, 13 ko": the team hears the latest offer
            return "close", self.close(text, lang)
        if not no and d is None and caller_words.agrees(text):  # "haan, kar dungi": by <latest>
            self.state = "done"
            logger.info("RULE promise_date_far_earlier: yes, %s", self.latest())
            return "promise", self.latest()
        if no or caller_words.DECLINE.search(text):
            return "close", self.close(text, lang)
        # can't be read ("నైన్", "ఆండీ", nothing at all): not a no, so ask once more
        if self.unclear < MAX_UNCLEAR:
            self.unclear += 1
            logger.info("RULE promise_date_far_unclear: %r, asking the same question again", text[:60])
            return "ask", self._earlier_question(lang)
        return "close", self.close(text, lang)

    def offered_note(self):
        who = self.call.promised_by or "customer"
        return f"{who} offered {say_offer(self.offered, for_note=True)}, beyond limit"

    def close(self, text, lang):
        """The fixed close: book the colleague's call at the next default-hour slot, no
        question about the day or time, and end the call."""
        self.state = "done"
        note = self.offered_note()
        book_followup(self.call, note, "human_callback")
        logger.info("RULE promise_date_far_close: %r, %s, ending the call", text[:60], note)
        lines = caller_words.FAR_DATE_CLOSE_TP if self._tp() else caller_words.FAR_DATE_CLOSE
        return Reply(self._line(lines, lang, date=say_offer(self.offered, lang)), hang_up=True)
