"""When someone other than the customer answers. A state machine: the code asks every
question and reads every answer, and the AI says nothing until the person is verified
or the flow has ended.

    relationship -> consent -> dob -> verified (the AI takes over, in the third person)
         |            |  no     |  wrong 3 times / doesn't know
         |            v         v
         +-------> availability (when can we reach the customer?) -> close

With THIRD_PARTY_DISCLOSURE=none there is no consent or DOB step: nothing is shared.
Someone who then says "actually main hi Sunita hoon" must give the customer's own
date of birth (claim_dob) before anything is said about the loan.
"""
import logging
import re
from dataclasses import dataclass

from tools import caller_words, policy, spoken
from tools.dates import say_when

logger = logging.getLogger("tam-voice-agent")

DONT_KNOW = re.compile(
    r"pata\s+nahi|nahi\s+pata|maloom\s+nahi|yaad\s+nahi|don'?t\s+know|not\s+sure|no\s+idea"
    r"|पता\s+नहीं|नहीं\s+पता|मालूम\s+नहीं|याद\s+नहीं",
    re.I)
MAX_TIME_MISSES = 2  # unclear or out-of-hours answers before closing without a time


@dataclass
class Reply:
    line: str | None = None  # what the code says; None when the AI should take over
    hang_up: bool = False
    verified: str | None = None  # "third_party" or "customer" when the check passed


class ThirdPartyFlow:
    def __init__(self, call):
        self.call = call
        self.state = None
        self.unclear = 0
        self.time_misses = 0

    @property
    def active(self):
        return self.state not in (None, "done")

    @property
    def waiting_for_dob(self):
        return self.state in ("dob", "claim_dob")

    def _line(self, lines, lang, **values):
        return caller_words.line(lines, lang, name=self.call.first_name, **values)

    # ---- the person who answered said they are someone else ----
    def start(self, text, lang):
        call = self.call
        if caller_words.wrong_number(text):
            call.report_person("third_party")
            call.record_outcome("wrong_number")
            self.state = "done"
            logger.info("RULE wrong_number: %r", text[:60])
            return Reply(self._line(caller_words.WRONG_NUMBER_CLOSE, lang), hang_up=True)
        call.report_person("third_party")
        call.third_party = {
            "mode": policy.THIRD_PARTY_DISCLOSURE,
            "name": caller_words.other_name(text, call.first_name),
            "relationship": caller_words.relationship(text),
            "consent": None, "dob": None, "shared": False,
        }
        logger.info("RULE third_party_flow: started, name=%s, relationship=%s",
                    call.third_party["name"], call.third_party["relationship"])
        prefix = (self._line(caller_words.TP_PERSONAL, lang) + " "
                  if caller_words.asks_why_calling(text) else "")
        if call.third_party["relationship"]:
            return self._after_relationship(lang, prefix)
        self.state = "relationship"
        return Reply(prefix + self._line(caller_words.TP_RELATIONSHIP, lang))

    def hear(self, text, lang):
        tp, state = self.call.third_party, self.state
        if (state != "claim_dob" and self.call.person_corrections == 0
                and caller_words.claims_to_be_customer(text, self.call.first_name)):
            return self._claim(lang)
        if state == "availability":
            return self._availability(text, lang)
        if state in ("dob", "claim_dob"):
            return self._dob(text, lang)
        if caller_words.asks_why_calling(text) and caller_words.consent(text) != "no":
            return Reply(self._line(caller_words.TP_PERSONAL, lang) + " " + self._question(lang))
        if state == "relationship":
            tp["relationship"] = caller_words.relationship(text) or text.strip()[:40]
            tp["name"] = tp["name"] or caller_words.other_name(text, self.call.first_name)
            return self._after_relationship(lang)
        answer = caller_words.consent(text)
        if answer == "yes":
            tp["consent"] = "yes"
            self.state = "dob"
            return Reply(self._line(caller_words.TP_DOB, lang))
        if answer is None and self.unclear == 0:
            self.unclear += 1
            return Reply(self._line(caller_words.TP_CONSENT_AGAIN, lang))
        tp["consent"] = answer or "unclear"
        return self._no_share(lang)

    def _question(self, lang):
        """The question the flow is waiting on, to ask again."""
        return {"relationship": self._line(caller_words.TP_RELATIONSHIP, lang),
                "consent": self._line(caller_words.TP_CONSENT, lang)}[self.state]

    def _after_relationship(self, lang, prefix=""):
        if policy.THIRD_PARTY_DISCLOSURE == "none":
            self.call.third_party["consent"] = "not_asked"
            return self._no_share(lang, prefix)
        self.state = "consent"
        return Reply(prefix + self._line(caller_words.TP_CONSENT, lang))

    def _no_share(self, lang, prefix=""):
        self.state = "availability"
        return Reply(prefix + self._line(caller_words.TP_NO_SHARE, lang))

    # ---- "actually main hi Sunita hoon": the customer's own date of birth first ----
    def _claim(self, lang):
        call = self.call
        call.person_corrections += 1
        call.person_status = "customer"
        call.needs_dob = True
        self.state = "claim_dob"
        logger.info("RULE third_party_claims_customer: asking for the date of birth")
        return Reply(self._line(caller_words.DOB_QUESTION, lang))

    def _dob(self, text, lang):
        call, tp = self.call, self.call.third_party
        claim = self.state == "claim_dob"
        if DONT_KNOW.search(text) or (caller_words.declines(text) and spoken.parse_dob(text) == "no_date"):
            tp["dob"] = "not_given"
            if claim:
                call.person_status = "third_party"
            return self._no_share(lang)
        said = spoken.parse_dob(text)
        if isinstance(said, tuple):
            result = call.check_dob(*said)
        else:  # "no_date" or "two_digit_year": not a try, but it counts towards the limit
            call.impossible_dates += 1
            result = {"verified": False, "error": said, "locked": call._locked()}
        logger.info("RULE dob_parsed_by_code: %s -> %s", said,
                    "match" if result["verified"] else result.get("error", "no match"))
        if result["verified"]:
            tp["dob"] = "verified"
            self.state = "done"
            if claim:
                call.dob_verified = True
                self._log_disclosure()
                return Reply(verified="customer")
            tp["shared"] = True
            self._log_disclosure()
            return Reply(verified="third_party")
        if result.get("locked"):
            tp["dob"] = "failed"
            if claim:
                call.person_status = "third_party"
            self.state = "availability"
            logger.info("RULE dob_failed: no match after the last try, sharing nothing")
            return Reply(self._line(caller_words.TP_DOB_FAILED, lang))
        if result.get("error") == "two_digit_year":
            return Reply(self._line(caller_words.DOB_FULL_YEAR, lang))
        if result.get("error") == "no_date":
            return Reply(self._line(caller_words.DOB_NOT_HEARD, lang))
        retry = caller_words.DOB_RETRY if claim else caller_words.TP_DOB_RETRY
        return Reply(self._line(retry, lang))

    # ---- when can we reach the customer? ----
    def _availability(self, text, lang):
        call = self.call
        if DONT_KNOW.search(text):
            return self._close(lang)
        said = spoken.callback_time(text, call.clock())
        if "when" in said:
            w = said["when"]
            result = call.schedule_callback(w.day, w.month, w.year, w.hour, w.minute,
                                            "customer_unavailable")
            if result.get("saved"):
                return self._close(lang, when=w)
            missing = None
        else:
            missing = said["missing"]
            if missing == "both" and caller_words.declines(text):
                return self._close(lang)
        self.time_misses += 1
        if self.time_misses >= MAX_TIME_MISSES:
            return self._close(lang)
        if missing:
            return Reply(self._line(caller_words.TIME_INCOMPLETE, lang))
        return Reply(time_outside_line(lang))

    def _close(self, lang, when=None):
        self.state = "done"
        self.call.record_outcome("third_party")
        self._log_disclosure()
        if when is not None:
            return Reply(self._line(caller_words.TP_CLOSE_TIME, lang, when=say_when(when, lang)),
                         hang_up=True)
        return Reply(self._line(caller_words.TP_CLOSE, lang), hang_up=True)

    def _log_disclosure(self):
        tp = self.call.third_party
        logger.info("RULE third_party_disclosure: mode=%s, relationship=%s, consent=%s, dob=%s, shared=%s",
                    tp["mode"], tp["relationship"], tp["consent"], tp["dob"], tp["shared"])


def time_outside_line(lang):
    """"We only call between 8 am and 7 pm", from the calling hours in tools/policy."""
    start = policy.CALL_WINDOW_START % 12 or 12
    end = policy.CALL_WINDOW_END % 12 or 12
    return caller_words.line(caller_words.TIME_OUTSIDE, lang, start=start, end=end)
