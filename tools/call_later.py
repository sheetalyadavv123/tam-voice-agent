"""The customer asks to be called later ("abhi busy hoon", "Monday call karna"): the code
asks for a day and time, books the callback and closes. The AI says nothing here.

Starts when the customer confirms who they are but is busy, when the AI reports them
"unavailable", or when a verified customer asks to be called later mid-call.
"""
import logging
import re

from tools import caller_words
from tools.callback_ask import TimeAsk, book, say_day, says_any_time
from tools.dates import say_when
from tools.third_party import DONT_KNOW, MAX_TIME_MISSES, Reply, time_outside_line

logger = logging.getLogger("tam-voice-agent")

# Mid-call, only a clear ask to be called later counts; "baad mein pay karunga" is a
# payment answer, not this.
ASKS_CALL_LATER = re.compile(
    r"\b(?:busy|occupied|driving|in\s+a\s+meeting|meeting\s+m(?:ein|e|ai|ain)|abhi\s+baat\s+nahi"
    r"|abhi\s+time\s+nahi|abhi\s+waqt\s+nahi"
    r"|call\s+(?:me\s+)?(?:back|later|tomorrow|on\s+\w+|\w+day|kal|parson|next\s+\w+)"
    r"|(?:can|could|will)\s+you\s+(?:please\s+)?call\s+(?:me\s+)?(?:back|later)"
    r"|talk\s+later|not\s+a\s+good\s+time|bad\s+time"
    r"|(?:baad|later)\s+(?:m(?:ein|e|ai)\s+)?(?:call|phone|fon|baat)"
    r"|(?:call|phone|fon)\s+(?:kar(?:na|o|iye|ein|en|ie|ke|\s+lena|\s+lo|\s+dena)|kijiye|kariyega))\b"
    r"|व्यस्त|बिज़ी|बिजी|अभी\s+बात\s+नहीं|अभी\s+(?:टाइम|time|समय|वक्त)\s+नहीं"
    r"|बाद\s+में\s+(?:कॉल|फ़ोन|फोन|बात)"
    r"|(?:कॉल|call|फ़ोन|फोन)\s+(?:करना|करो|करिए|करिये|कीजिए|करें|करियेगा|करिएगा|कर\s+लेना|कर\s+लो|कर\s+लीजिए)",
    re.I)
# At the greeting only ("am I speaking with Sunita?" -> "abhi nahi", "baad mein"). Mid-call
# these answer "when will you pay?". "Sunita abhi nahi hai" (she isn't here) is not this.
GREETING_CALL_LATER = re.compile(
    r"\babhi\s+(?:nahi|nahin|nhi)\b(?!\s+(?:hai|hain|h|he|hei|hoti|hota|hongi|honge|aayi|aaye|aaenge)\b)"
    r"|\bbaad\s+m(?:ein|e|ai)\b|\blater\b"
    r"|अभी\s+(?:नहीं|नही)(?!\s+(?:है|हैं|होंगी|होंगे|आई|आए|आएंगे|आएंगी))|बाद\s+में",
    re.I)
# Talking about someone else ("she's busy", "वो abhi busy hai"): the third-party flow
ABOUT_SOMEONE = re.compile(
    r"\b(?:she|he|they|wo|woh|vo|voh|unka|unki|unke|uska|uski|her|his)\b|(?:^|\s)(?:वो|वह|वे|उनका|उनकी|उनके)(?:\s|$)",
    re.I)


# "Abhi busy hoon, Friday tak pay kar dunga" is a payment answer: the AI takes it
PAY_TALK = re.compile(
    r"\b(?:pay|payment|emi|bhugtan|paise|paisa|tak|(?:kar|de|bhar)\s+d(?:u|oo)ng[ia]"
    r"|(?:kar|de|bhar)\s+deng[ei])\b"
    r"|भुगतान|पेमेंट|पैसे|पैसा|तक|(?:कर|दे|भर)\s+(?:दूंग|दूँग|देंग)",
    re.I)


def asks_call_later(text, greeting=False):
    """"Abhi busy hoon", "I'm occupied", "call me on Monday", "baad mein call karna";
    not when the same message talks about paying. greeting: also "abhi nahi", "baad
    mein", "later", which only mean this as the answer to "am I speaking with Sunita?"."""
    text = text or ""
    found = ASKS_CALL_LATER.search(text) or (greeting and GREETING_CALL_LATER.search(text))
    return bool(found) and not PAY_TALK.search(text)


def busy_before_identity(text, first_name):
    """The first answer only says busy / call me later, with no yes, no name and nothing
    about someone else: "Actually, I'm quite occupied ... call me on Monday?"."""
    return (asks_call_later(text, greeting=True) and not caller_words.YES_FIRST.search(text)
            and not caller_words.IT_IS_ME.search(text) and not ABOUT_SOMEONE.search(text)
            and not caller_words.says_not_the_customer(text)
            and not caller_words.other_name(text, first_name)
            and not caller_words.wrong_number(text, first_name))


class CallLaterFlow:
    def __init__(self, call):
        self.call = call
        self.active = False
        self.done = False  # the flow ran and closed: no colleague talk after it either
        self.ask = TimeAsk(call.clock)
        self.misses = 0
        # False when nobody said yes to "am I speaking with Sunita?": the callback is for
        # the customer (customer_unavailable) and nothing about the loan is shared
        self.confirmed = True

    def _line(self, lines, lang, **values):
        return caller_words.line(lines, lang, name=self.call.first_name, **values)

    def start(self, text, lang, confirmed=True):
        """The first message may already have the day and time ("Monday 5 baje call karna")."""
        self.active, self.confirmed = True, confirmed
        logger.info("RULE call_later: %r, asking when to call%s", text[:60],
                    "" if confirmed else " (identity not confirmed, sharing nothing)")
        return self._read(text, lang, first=True)

    def hear(self, text, lang):
        return self._read(text, lang)

    @property
    def reason(self):
        return "customer_requested" if self.confirmed else "customer_unavailable"

    def _read(self, text, lang, first=False):
        call = self.call
        if (not first and not self.ask.waiting and DONT_KNOW.search(text)
                and not says_any_time(text)):
            return self._close(lang)
        said = self.ask.read(text)
        if "ask_time" in said:
            logger.info("RULE callback_time_ask: day %s given without a time, asking once",
                        said["ask_time"])
            return Reply(self._line(caller_words.TIME_FOR_DAY, lang, day=say_day(said["ask_time"])))
        if "when" in said:
            result = book(call, said, self.reason)
            if result.get("saved"):
                return self._close(lang, said["when"], said.get("note"))
            if first:
                self.ask.mark_asked()
                return Reply(time_outside_line(lang))
        elif first:
            return Reply(self._line(caller_words.CALL_LATER_ASK, lang))
        elif said["missing"] == "both" and caller_words.declines(text):
            return self._close(lang)
        self.misses += 1
        if self.misses >= MAX_TIME_MISSES:
            return self._close(lang)
        self.ask.mark_asked()  # the time is asked for once at most
        if "when" in said:
            return Reply(time_outside_line(lang))
        return Reply(self._line(caller_words.TIME_INCOMPLETE, lang))

    def _close(self, lang, when=None, note=None):
        self.active, self.done = False, True
        call = self.call
        if when is None:
            call.record_outcome_or_better("not_available", "asked to call later, no time given")
            logger.info("RULE call_later_close: no time given")
            return Reply(self._line(caller_words.CALL_LATER_CLOSE_NO_TIME, lang), hang_up=True)
        if self.confirmed:
            call.record_outcome_or_better("callback_requested", note or "")
        else:
            call.record_outcome_or_better(
                "not_available", "identity not confirmed, callback booked" + (f"; {note}" if note else ""))
        logger.info("RULE call_later_close: callback at %s%s", when.isoformat(),
                    f" ({note})" if note else "")
        if note:
            return Reply(self._line(caller_words.CALL_LATER_CLOSE_DAY, lang, day=say_day(when)),
                         hang_up=True)
        return Reply(self._line(caller_words.CALL_LATER_CLOSE, lang, when=say_when(when, lang)),
                     hang_up=True)
