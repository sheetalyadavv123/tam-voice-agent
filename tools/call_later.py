"""The customer asks to be called later ("abhi busy hoon", "Monday call karna"): the code
asks for a day and time, books the callback and closes. The AI says nothing here.

Starts when the customer confirms who they are but is busy, when the AI reports them
"unavailable", or when a verified customer asks to be called later mid-call.
"""
import logging
import re

from tools import caller_words
from tools.callback_ask import TimeAsk, book, say_day
from tools.collection_tools import DAY_ONLY_NOTE
from tools.dates import say_when
from tools.third_party import DONT_KNOW, MAX_TIME_MISSES, Reply, time_outside_line

logger = logging.getLogger("tam-voice-agent")

# Mid-call, only a clear ask to be called later counts; "baad mein pay karunga" is a
# payment answer, not this.
ASKS_CALL_LATER = re.compile(
    r"\b(?:busy|driving|in\s+a\s+meeting|meeting\s+m(?:ein|e|ai|ain)|abhi\s+baat\s+nahi"
    r"|call\s+(?:me\s+)?(?:back|later)|talk\s+later|not\s+a\s+good\s+time"
    r"|(?:baad|later)\s+(?:m(?:ein|e|ai)\s+)?(?:call|phone|fon|baat)"
    r"|(?:call|phone|fon)\s+(?:kar(?:na|o|iye|ein|en|ie|ke)|kijiye|kariyega))\b"
    r"|व्यस्त|बिज़ी|बिजी|अभी\s+बात\s+नहीं|बाद\s+में\s+(?:कॉल|फ़ोन|फोन|बात)"
    r"|(?:कॉल|फ़ोन|फोन)\s+(?:करना|करो|करिए|करिये|कीजिए|करें|करियेगा|करिएगा)",
    re.I)


# "Abhi busy hoon, Friday tak pay kar dunga" is a payment answer: the AI takes it
PAY_TALK = re.compile(
    r"\b(?:pay|payment|emi|bhugtan|paise|paisa|tak|(?:kar|de|bhar)\s+d(?:u|oo)ng[ia]"
    r"|(?:kar|de|bhar)\s+deng[ei])\b"
    r"|भुगतान|पेमेंट|पैसे|पैसा|तक|(?:कर|दे|भर)\s+(?:दूंग|दूँग|देंग)",
    re.I)


def asks_call_later(text):
    """"Abhi busy hoon", "baad mein call karna", "Monday call karo"; not when the same
    message talks about paying."""
    return bool(ASKS_CALL_LATER.search(text or "")) and not PAY_TALK.search(text or "")


class CallLaterFlow:
    def __init__(self, call):
        self.call = call
        self.active = False
        self.ask = TimeAsk(call.clock)
        self.misses = 0

    def _line(self, lines, lang, **values):
        return caller_words.line(lines, lang, name=self.call.first_name, **values)

    def start(self, text, lang):
        """The first message may already have the day and time ("Monday 5 baje call karna")."""
        self.active = True
        logger.info("RULE call_later: %r, asking when to call", text[:60])
        return self._read(text, lang, first=True)

    def hear(self, text, lang):
        return self._read(text, lang)

    def _read(self, text, lang, first=False):
        call = self.call
        if not first and not self.ask.waiting and DONT_KNOW.search(text):
            return self._close(lang)
        said = self.ask.read(text)
        if "ask_time" in said:
            logger.info("RULE callback_time_ask: day %s given without a time, asking once",
                        said["ask_time"])
            return Reply(self._line(caller_words.TIME_FOR_DAY, lang, day=say_day(said["ask_time"])))
        if "when" in said:
            w, day_only = said["when"], said.get("day_only", False)
            result = book(call, said, "customer_requested")
            if result.get("saved"):
                return self._close(lang, w, day_only)
            if first:
                return Reply(time_outside_line(lang))
        elif first:
            return Reply(self._line(caller_words.CALL_LATER_ASK, lang))
        elif said["missing"] == "both" and caller_words.declines(text):
            return self._close(lang)
        self.misses += 1
        if self.misses >= MAX_TIME_MISSES:
            return self._close(lang)
        if "when" in said:
            return Reply(time_outside_line(lang))
        return Reply(self._line(caller_words.TIME_INCOMPLETE, lang))

    def _close(self, lang, when=None, day_only=False):
        self.active = False
        call = self.call
        if when is None:
            call.record_outcome_or_better("not_available", "asked to call later, no time given")
            logger.info("RULE call_later_close: no time given")
            return Reply(self._line(caller_words.CALL_LATER_CLOSE_NO_TIME, lang), hang_up=True)
        call.record_outcome_or_better("callback_requested", DAY_ONLY_NOTE if day_only else "")
        logger.info("RULE call_later_close: callback at %s%s", when.isoformat(),
                    " (day only)" if day_only else "")
        if day_only:
            return Reply(self._line(caller_words.CALL_LATER_CLOSE_DAY, lang, day=say_day(when)),
                         hang_up=True)
        return Reply(self._line(caller_words.CALL_LATER_CLOSE, lang, when=say_when(when, lang)),
                     hang_up=True)
