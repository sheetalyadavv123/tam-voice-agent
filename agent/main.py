import asyncio
import json
import logging
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal  # noqa: F401  (kept for tool signatures)
from urllib.parse import urlparse

import asyncpg
# Let this file import the "tools" folder in the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from livekit import agents, rtc
from livekit.agents import (
    Agent, AgentSession, MetricsCollectedEvent, ModelSettings, RunContext, StopResponse,
    function_tool, get_job_context, llm, metrics, room_io,
)
from livekit.plugins import (
    deepgram, elevenlabs, google, noise_cancellation, openai, sarvam, silero,
)

import config
import db
from tools import caller_words, dates, precedence, relative_dates, rules, schema_check
from tools.call_later import CallLaterFlow, asks_call_later, busy_before_identity
from tools.callback_ask import TimeAsk, book, say_day
from tools.collection_tools import (
    AiCallbackReason, Call, Channel, HandoffReason, Outcome, PersonStatus,
)
from tools.dates import say_when
from tools.far_date import (
    FarDateFlow, hardship_close, hardship_no_date_close, latest_allowed, say_date,
)
from tools.relative_dates import last_offer, single
from tools.language import LanguageTracker, turn_language
from tools.speech_guard import ReplyGuard
from tools.third_party import ThirdPartyFlow, time_outside_line
from tools.store import NO_CALLER, MemoryStore
from tools.pg_store import PostgresStore

logger = logging.getLogger("tam-voice-agent")
ROOT = Path(__file__).parent.parent
BASE_PROMPT = (ROOT / "prompts" / "collections.md").read_text(encoding="utf-8").replace(
    "{company}", config.COMPANY_NAME)
TRANSCRIPT_DIR = ROOT / "transcripts"
LOG_KEYS = ("ok", "next", "error", "reason", "status", "saved", "sent", "verified",
            "requested", "locked", "duplicate", "source", "loan_closed")
REFUSED_KEYS = ("ok", "saved", "sent", "verified", "requested")
USER_AWAY_SECONDS = 8
# Below the bridge's URL_TTL (30 s from /sampark, after which it deletes an unused room):
# the agent's wait starts seconds after /sampark, so a 30 s wait would outlive the room.
# If the room still goes first (a slow start), finish() records the same outcome.
CALLER_WAIT_SECONDS = 25


def build_llm():
    if config.LLM_PROVIDER == "openai":
        extra = {}
        if config.LLM_REASONING_EFFORT:
            extra["reasoning_effort"] = config.LLM_REASONING_EFFORT
        return openai.LLM(model=config.LLM_MODEL, **extra)
    return google.LLM(model=config.LLM_MODEL)


def build_tts():
    if config.TTS_PROVIDER == "sarvam":
        logger.info("TTS: sarvam bulbul:v3 speaker=%s pace=%s", config.SARVAM_SPEAKER, config.TTS_PACE)
        return sarvam.TTS(
            target_language_code=config.SARVAM_LANGUAGE,
            model="bulbul:v3",
            speaker=config.SARVAM_SPEAKER,
            pace=config.TTS_PACE,
        )
    logger.info("TTS: elevenlabs %s voice=%s", config.TTS_MODEL, config.TTS_VOICE_ID)
    return elevenlabs.TTS(model=config.TTS_MODEL, voice_id=config.TTS_VOICE_ID)


_schema_ok = False  # set once a check passed in this process; a mismatch is checked again


async def check_schema():
    """True when the database has every table and column the code needs, False (and
    "SCHEMA MISMATCH: ..." at ERROR) when it doesn't, None when it couldn't be checked
    (no database, or it can't be reached: build_store handles that)."""
    global _schema_ok
    if _schema_ok or not config.DATABASE_URL:
        return True if _schema_ok else None
    try:
        conn = await asyncio.wait_for(asyncpg.connect(config.DATABASE_URL), timeout=15)
        try:
            result = await schema_check.check(conn)
        finally:
            await conn.close()
    except Exception as e:
        logger.warning("schema check skipped: could not reach the database (%s)", type(e).__name__)
        return None
    if result["missing"]:
        logger.error(schema_check.message(result))
        return False
    _schema_ok = True
    return True


def schema_gate(ok, test_mode):
    """What a call does after the schema check: "refuse" (TEST_MODE off and a mismatch),
    "memory" (TEST_MODE on and a mismatch: in-memory data, nothing saved) or "go"."""
    if ok is False:
        return "memory" if test_mode else "refuse"
    return "go"


async def build_store(customer_id):
    """Returns (store, persistent). store is None when we must not place the call."""
    if config.DATABASE_URL:
        try:
            return await PostgresStore.load(config.DATABASE_URL, [customer_id]), True
        except Exception:
            logger.exception("could not load data from the database")
    if config.TEST_MODE or not config.DATABASE_URL:
        logger.warning("using in-memory data: nothing will be saved between calls")
        return MemoryStore.from_files(clock=rules.now_ist), False
    return None, False


async def wait_for_caller(ctx, timeout=None):
    """The greeting waits until the caller is in the room. SAMPARK rooms are created at
    /sampark, while the dialer is still connecting, so the agent can get there first and
    session.start() doesn't wait for anyone. False if nobody comes within timeout."""
    if ctx.is_fake_job():  # console mode: a mock room, the caller is the terminal
        return True
    try:
        participant = await asyncio.wait_for(ctx.wait_for_participant(),
                                             CALLER_WAIT_SECONDS if timeout is None else timeout)
    except asyncio.TimeoutError:
        return False
    logger.info("RULE greet_after_join: %s is in the room", participant.identity)
    return True


def pick_target(ctx):
    """Which customer is this call for? Room metadata (SAMPARK calls) wins, then job
    metadata, then the test setting."""
    meta = {}
    room = getattr(ctx.job, "room", None)
    try:
        meta = json.loads(getattr(room, "metadata", "") or "{}")
    except ValueError:
        pass
    if meta.get("customer_id"):
        kind = "followup" if meta.get("kind") == "followup" else "reminder"
        logger.info("RULE target_from_room: customer %s, call %s",
                    meta["customer_id"], meta.get("call_number"))
        return meta["customer_id"], kind
    meta = {}
    try:
        meta = json.loads(ctx.job.metadata or "{}")
    except ValueError:
        logger.warning("bad job metadata ignored")
    customer_id = meta.get("customer_id") or os.getenv("TEST_CUSTOMER_ID", "C002")
    kind = "followup" if meta.get("kind") == "followup" else "reminder"
    return customer_id, kind


class Collector(Agent):
    def __init__(self, call: Call, instructions: str):
        super().__init__(instructions=instructions)
        self.call = call
        self.base_instructions = instructions
        self.language = LanguageTracker(call.language)
        self.flow = ThirdPartyFlow(call)  # someone else answered: the code runs that part
        self.notes = {}  # sections added to the instructions during the call, by name
        self.silences = 0  # times in a row the caller went quiet
        self.identity_asks = 0  # "Okay" at the greeting: the code asks again, twice at most
        self.time_misses = 0  # hardship callback: answers without a usable time
        self.time_ask = TimeAsk(call.clock)  # hardship callback: a day without a time
        self.later = CallLaterFlow(call)  # the customer asks to be called later
        self.busy_text = None  # "I'm busy" before any yes, while we ask who it is
        self.busy_asked = False  # that question is asked once per call
        self.far = FarDateFlow(call)  # a pay date beyond MAX_PROMISE_DAYS
        self._end_task = None

    async def on_user_turn_completed(self, turn_ctx, new_message):
        text = new_message.text_content or ""
        self.call.heard(text)
        self.silences = 0
        await self._follow_language(turn_ctx, text)
        agreed = self.call.caller_agreed_to(self._last_agent_text(), text)
        if agreed:
            logger.info("RULE promise_date_agreed: %s (link waits for record_promise_to_pay)", agreed)
        line, hang_up = await self._code_reply(turn_ctx, text)
        if line:
            # LiveKit drops a stopped turn's message; keep it so the transcript has it
            await self._keep_in_history(new_message)
            self._say(line, hang_up)
            raise StopResponse()
        if (turn_ctx is not None and self.call.person_status is None
                and caller_words.confirms_identity(text, self.call.first_name)):
            # a yes with "busy" in it is left to the AI; the tool still checks
            turn_ctx.add_message(role="system", content=(
                f"The caller just confirmed they are {self.call.first_name}. "
                "Call report_person with status customer now."))
        self._add_days_note(turn_ctx, text)

    def _add_days_note(self, turn_ctx, text):
        """The days the caller named ("Friday", "दस दिन बाद", "12 को"), as dates, for the AI."""
        today = self.call.clock().date()
        days = relative_dates.note_for(text, today)
        if days:
            logger.info("RULE weekday_resolved: %s", days)
        if days and turn_ctx is not None:
            turn_ctx.add_message(role="system", content=(
                f"The caller named these days (IST): {days}. Use exactly these dates; for a "
                "range, the promise date is its last day."))
            promise = self.call.promise
            new = [d for _, d in relative_dates.pay_dates(text, today)]
            if promise and promise["date"] not in new:
                logger.info("RULE promise_date_change: caller named %s, promise is %s",
                            new[-1], promise["date"])
                turn_ctx.add_message(role="system", content=(
                    f"The saved promise is {dates.say(promise['date'])}, but the caller named "
                    f"{dates.say(new[-1])}. If they are changing the promise date, call "
                    "record_promise_to_pay with the new date; it replaces the old one."))

    async def _follow_language(self, turn_ctx, text):
        if self.flow.waiting_for_dob:  # "2 November 1985" says nothing about the language
            logger.info("RULE language_hold: waiting for a date of birth (%r)", text[:60])
            return
        switched = self.language.observe(text)
        if not switched:
            if turn_language(text) not in (None, self.language.current):
                logger.info("RULE language_hold: staying in %s for %r", self.language.current, text[:60])
            return
        name = "English" if switched == "en" else "Hindi"
        logger.info("RULE language_switch: now %s after %r", name, text[:60])
        rule = (f"# Language now\nThe customer is speaking {name}. "
                f"Reply only in {name} from now on.")
        # the instructions cover later turns; turn_ctx is this reply's copy
        await self._add_note("language", rule)
        if turn_ctx is not None:
            turn_ctx.add_message(role="system", content=rule)
        if isinstance(self.session.tts, sarvam.TTS):
            self.session.tts.update_options(
                target_language_code="en-IN" if switched == "en" else config.SARVAM_LANGUAGE)

    async def _add_note(self, name, text):
        self.notes[name] = text
        await self.update_instructions(
            self.base_instructions + "".join("\n" + n + "\n" for n in self.notes.values()))

    async def _code_reply(self, turn_ctx, text):
        """Every part of the call the code runs itself: who answered, the third-party
        check, hardship and the colleague's callback time, plus the fixed answers below.
        Returns (line, hang_up); line is None to let the AI reply."""
        call = self.call
        if self.flow.active:
            return await self._flow_reply(self.flow.hear(text, self.language.current))
        if self.later.active:
            reply = self.later.hear(text, self.language.current)
            return reply.line, reply.hang_up
        if call.person_status is None:
            reply = await self._identity_reply(text)
            if reply[0] or call.person_status is None:
                return reply
        line, hang_up, handled = await self._paths(turn_ctx, text)  # tools/precedence order
        if handled:  # a line, or the AI replies to this path with nothing else checked
            return line, hang_up
        line, hang_up = self._fixed_reply(text)
        if line:
            return line, hang_up
        line = self._cant_commit_reply(text)
        if line:
            return line, False
        line, hang_up = self._call_later_reply(text)
        if line:
            return line, hang_up
        return self._colleague_time_reply(text)

    def _call_later_reply(self, text):
        """The customer (not someone else) asks to be called later, before any promise and
        outside hardship or a dispute: the code asks for a day and time and books it."""
        call = self.call
        if (call.person_status not in ("customer", "unavailable") or call.promise_recorded
                or call.push_stopped or call.hardship is not None or self.later.active
                or call.callback_id is not None or not asks_call_later(text)):
            return None, False
        reply = self.later.start(text, self.language.current)
        return reply.line, reply.hang_up

    async def _paths(self, turn_ctx, text, offer=None):
        """Every rule about legal questions, a wrong number, pay dates and hardship, in the
        one order tools/precedence.route decides. offer: a date the AI's
        record_promise_to_pay found too far; else the date is read from the caller's words.
        Returns (line, hang_up, handled): handled is True when this path took the turn (a
        line from the code, or the AI replies to it with no other check run)."""
        call, lang = self.call, self.language.current
        if not call.unlocked:
            return None, False, False
        if offer is None and not (asks_call_later(text) and not self.far.active):
            offer = last_offer(text, call.clock().date())  # "10 din baad call karna" is not one
        if offer is None and self.far.active:
            offer = self.far.offered  # asking for an earlier date: the date already offered
        path = precedence.route(text, call.first_name, offer=offer, latest=latest_allowed(call),
                                hardship_before=call.hardship in ("asked_date", "with_date"),
                                push_stopped=bool(call.push_stopped))
        if path:
            logger.info("RULE path: %s for %r", path, text[:60])
        if path == precedence.LEGAL:
            logger.warning("RULE legal_handoff: %s", text[:120])
            self.far.state = "done" if self.far.active else self.far.state
            call.transfer_to_human("legal_question")
            call.record_outcome("human_callback", "legal question")
            return caller_words.line(caller_words.LEGAL_LINE, lang), True, True
        if path == precedence.WRONG_NUMBER:
            self.far.state = "done" if self.far.active else self.far.state
            reply = self.flow._wrong_number(text, lang, "mid-call")
            return reply.line, reply.hang_up, True
        if path == precedence.HARDSHIP_FAR:
            return (*self._hardship_far(offer), True)
        if self.far.active:  # an earlier date, a yes, a no, or something unclear
            if path == precedence.HARDSHIP_WITHIN:
                self._hardship_within(None)
            return (*self._far_reply(turn_ctx, text), True)
        if path == precedence.FAR:
            if call.promise_recorded or self.far.state is not None:
                return None, False, False  # changing a saved promise: the AI and its tool
            return self.far.start(offer, lang).line, False, True
        if path == precedence.HARDSHIP_WITHIN:
            self._hardship_within(turn_ctx)
            return None, False, True
        if path == precedence.HARDSHIP_NO_DATE and call.hardship is None:
            call.hardship = "asked_date"
            logger.info("RULE hardship_date_ask: %r, asking once for a date", text[:60])
            lines = (caller_words.HARDSHIP_DATE_ASK_TP if self._talking_to_someone_else()
                     else caller_words.HARDSHIP_DATE_ASK)
            return self._line(lines, name=call.first_name), False, True
        if call.hardship == "asked_date" and path in (None, precedence.HARDSHIP_NO_DATE):
            return (*self._hardship_no_date(), True)  # asked once, still no date
        return None, False, False

    def _hardship_within(self, turn_ctx):
        """Hardship and a date within the limit: the promise closes the call, no colleague."""
        if self.call.hardship != "with_date":
            self.call.hardship = "with_date"
            logger.info("RULE hardship_with_date: the promise closes the call, no colleague")
        if turn_ctx is not None:
            turn_ctx.add_message(role="system", content=(
                "There is a money problem, but a day to pay was named. Be kind, read the date "
                "back and call record_promise_to_pay. Don't mention a colleague or a call back."))

    def _hardship_far(self, offer):
        """Hardship and a date beyond the limit: no earlier-date question, the kind close.
        A promise saved earlier in the call (hardship within the limit) is withdrawn, and
        the no-colleague rule ends with it."""
        call = self.call
        if call.promise_recorded:
            call.withdraw_promise("hardship, a later date beyond the limit")
        call.hardship = "far_date"  # not "with_date": the no-colleague rule is for dates within
        self.far.state = "done"
        reply = hardship_close(call, offer, self.language.current)
        return reply.line, reply.hang_up

    def _hardship_no_date(self):
        """Hardship, asked once for a date, none given: the kind close, no time question."""
        self.call.hardship = "no_date"
        reply = hardship_no_date_close(self.call, self.language.current)
        return reply.line, reply.hang_up

    def _far_reply(self, turn_ctx, text):
        """The answer to "can you pay by <latest>?": the promise, the same question once
        more (an answer that can't be read), or the fixed close."""
        kind, value = self.far.answer(text, self.language.current)
        if kind in ("ask", "close"):
            return value.line, value.hang_up
        d = value
        result = self._run("record_promise_to_pay", self.call.record_promise_to_pay,
                           d.day, d.month, d.year)
        if not result.get("saved"):
            return None, False  # the AI answers from the tool result
        logger.info("RULE promise_by_code: %s, recorded as usual", d)
        if turn_ctx is not None:
            turn_ctx.add_message(role="system", content=(
                f"The caller agreed to pay by {dates.say(d)}. The code already called "
                f"record_promise_to_pay and it is saved ({say_date(d)}). Confirm the amount "
                "and date in one sentence, then offer the payment link once. Don't mention a "
                "colleague or a call back."))
        return None, False

    def _cant_commit_reply(self, text):
        """A verified relative who can't say when the customer will pay ("pata nahi, unse
        hi poochna padega"): no promise; the code asks when to reach the customer, and the
        flow reads the time, books the callback and closes."""
        call = self.call
        if (not self._talking_to_someone_else() or call.promise_recorded or call.push_stopped
                or call.hardship is not None or self.flow.couldnt_commit
                or call.store.links_sent_in_session(call.session_id)
                or not caller_words.cant_commit(text) or self._pay_date_named(text)):
            return None
        return self.flow.cant_commit(text, self.language.current).line

    async def _after_busy(self, text):
        """The answer to the identity question asked after "I'm busy". A clear yes: the
        call-later flow. Someone else: the third-party flow. Anything else: the callback
        is booked anyway, as customer_unavailable, and nothing is shared. The day or "any
        time" may be in either message, so both are read."""
        call, name, lang = self.call, self.call.first_name, self.language.current
        busy, self.busy_text = self.busy_text, None
        if caller_words.wrong_number(text, name) or caller_words.someone_else(text, name):
            return await self._flow_reply(self.flow.start(text, lang))
        confirmed = caller_words.confirms_identity(text, name)
        call.report_person("unavailable")
        logger.info("RULE identity_busy_answer: %r, %s", text[:60],
                    "confirmed, asking when to call" if confirmed
                    else "not confirmed, booking the callback and sharing nothing")
        reply = self.later.start(f"{busy} . {text}", lang, confirmed=confirmed)
        return reply.line, reply.hang_up

    async def _identity_reply(self, text):
        """The answer to "am I speaking with Sunita?". A clear yes unlocks the call and the
        code gives the reminder; someone else starts the third-party flow."""
        call, name = self.call, self.call.first_name
        if self.busy_text is not None:  # we asked who it is after "I'm busy, call me Monday"
            return await self._after_busy(text)
        if not self.busy_asked and busy_before_identity(text, name):
            # "Actually, I'm quite occupied... call me on Monday?": no yes, so ask once
            self.busy_text, self.busy_asked = text, True
            logger.info("RULE identity_busy_unconfirmed: %r, asking who it is once", text[:60])
            return self._identity_question(), False
        if caller_words.wrong_number(text, name) or caller_words.someone_else(text, name):
            return await self._flow_reply(self.flow.start(text, self.language.current))
        if caller_words.only_filler(text) and self.identity_asks < 2:
            self.identity_asks += 1
            logger.info("RULE identity_not_confirmed: %r, asking again", text[:60])
            return self._identity_question(), False
        confirmed = caller_words.confirms_identity(text, name)
        if not confirmed and caller_words.fuzzy_name_only(text, name) and self.identity_asks < 2:
            # "Amita bol rahi hoon" for Amit: a close name alone doesn't unlock the call
            self.identity_asks += 1
            logger.info("RULE identity_fuzzy_name: %r is close to %s but not the name, asking again",
                        text[:60], name)
            return self._identity_question(), False
        if not confirmed:
            return None, False  # a question: the AI answers
        if asks_call_later(text):  # "haan, par abhi busy hoon": when should we call?
            call.report_person("unavailable")
            logger.info("RULE identity_busy: %r, the customer asks to be called later", text[:60])
            reply = self.later.start(text, self.language.current)
            return reply.line, reply.hang_up
        if caller_words.busy(text):
            return None, False  # "haan, busy hoon, Friday tak pay kar dunga": the AI answers
        call.report_person("customer")
        logger.info("RULE identity_confirmed_by_code: %r", text[:60])
        await self._add_note("who", f"# Who\nThe caller confirmed they are {name}. The code "
                                    "already gave the EMI reminder; carry on from there.")
        if caller_words.hardship(text):
            line, hang_up, handled = await self._paths(None, text)
            if line:
                return line, hang_up
        return self._reminder()

    async def _flow_reply(self, reply):
        if reply.verified == "third_party":
            tp, name = self.call.third_party, self.call.first_name
            who = " ".join(x for x in (tp.get("relationship"), tp.get("name")) if x) or "someone"
            await self._add_note("who", (
                f"# Speaking with\nYou are speaking with {name}'s {who}, who passed the check. "
                f"Talk about {name} in the third person (उनकी EMI, वे pay कर पाएंगे; "
                f"{name}'s EMI, they). Never say आपकी EMI. The payment link only goes to "
                f"{name}'s registered number, and you say so."))
            return self._reminder()
        if reply.verified == "customer":
            await self._add_note("who", f"# Who\nThe caller is {self.call.first_name}, verified "
                                        "by date of birth. The code already gave the EMI reminder.")
            line, hang_up = self._reminder()
            return self._line(caller_words.CLAIM_VERIFIED) + " " + line, hang_up
        return reply.line, reply.hang_up

    def _reminder(self):
        """The EMI reminder, spoken by the code: (line, hang_up)."""
        call = self.call
        due = call.get_due_details()
        if due.get("loan_closed"):
            logger.info("RULE loan_closed_close: no payment due, ending the call")
            call.record_outcome_or_better("no_resolution", "loan closed")
            return self._line(caller_words.LOAN_CLOSED), True
        d = date.fromisoformat(due["due_date"])
        values = {"amount": f"{due['emi_amount']:,}", "date": f"{d.day} {d:%B}",
                  "name": call.first_name}
        if self._talking_to_someone_else():
            return self._line(caller_words.TP_REMINDER, **values), False
        if "open_promise" in due:
            p = date.fromisoformat(due["open_promise"]["date"])
            return self._line(caller_words.DUE_QUESTION_WITH_PROMISE, promise=f"{p.day} {p:%B}",
                              **values), False
        return self._line(caller_words.DUE_QUESTION, **values), False

    def _talking_to_someone_else(self):
        return self.call.person_status == "third_party" and self.call.unlocked

    def _pay_date_named(self, text):
        today = self.call.clock().date()
        return bool(relative_dates.pay_dates(text, today) or caller_words.PAY_DAY.search(text))

    def _colleague_time_reply(self, text):
        """After a dispute, a time the caller gives for the colleague's call is read by the
        code, saved, and the call closes; other messages go to the AI. (Hardship never gets
        here: it always ends with the team's follow-up at the next default slot.)"""
        call = self.call
        if not call.push_stopped or call.callback_confirmed:
            return None, False
        said = self.time_ask.read(text)  # "kal" alone: asked once for the time, then the default hour
        if "ask_time" in said:
            logger.info("RULE callback_time_ask: day %s given without a time, asking once",
                        said["ask_time"])
            return self._line(caller_words.TIME_FOR_DAY, day=say_day(said["ask_time"])), False
        if "when" not in said:
            return None, False
        result = self._run("schedule_callback", book, call, said, "customer_requested")
        if result.get("saved"):
            return self._handoff_close(said["when"], said.get("note"))
        self.time_misses += 1
        self.time_ask.mark_asked()  # the time is asked for once at most
        if "when" in said:
            return time_outside_line(self.language.current), False
        return self._line(caller_words.TIME_INCOMPLETE), False

    def _handoff_close(self, when, note=None):
        """note: "caller gave day only" / "caller said any time" for the default hour."""
        if when is None:
            logger.info("RULE handoff_close: no time given, colleague calls at the next window")
            return self._line(caller_words.HANDOFF_CLOSE_NO_TIME, name=self.call.first_name), True
        if note:
            self.call.note = (self.call.note + "; " if self.call.note else "") + note
            logger.info("RULE handoff_close: colleague callback on %s (%s), ending the call",
                        when.date().isoformat(), note)
            return self._line(caller_words.HANDOFF_CLOSE_DAY, name=self.call.first_name,
                              day=say_day(when)), True
        logger.info("RULE handoff_close: colleague callback at %s, ending the call", when.isoformat())
        return self._line(caller_words.HANDOFF_CLOSE, name=self.call.first_name,
                          when=say_when(when, self.language.current)), True

    def _fixed_reply(self, text):
        """The link declined after a promise: the code closes. (Legal questions are a path in
        tools/precedence, run by _paths.) Returns (line, hang_up); line None lets the AI reply."""
        if (self.call.promise_recorded and not self.call.link_sent_after_promise
                and self._offered_link() and caller_words.declines(text)
                and not self._names_a_day(text)):  # "nahi, kal" changes the date, not the link
            logger.info("RULE promise_close: link declined after a promise (%r)", text[:60])
            self.call.record_outcome_or_better("promise_to_pay")
            return self._promise_close(caller_words.PROMISE_CLOSE_DECLINED), True
        return None, False

    def _line(self, lines, **values):
        return caller_words.line(lines, self.language.current, **values)

    def _promise_close(self, lines):
        if self._talking_to_someone_else():  # "the link went to Sunita's number"
            lines = (caller_words.PROMISE_CLOSE_SENT_TP if lines is caller_words.PROMISE_CLOSE_SENT
                     else caller_words.PROMISE_CLOSE_DECLINED_TP)
        p = self.call.promise
        return self._line(lines, name=self.call.first_name, amount=f"{p['amount']:,}",
                          date=f"{p['date'].day} {p['date']:%B}")

    def _dob_question(self):
        return self._line(caller_words.DOB_QUESTION, name=self.call.first_name)

    def _names_a_day(self, text):
        return bool(relative_dates.pay_dates(text, self.call.clock().date())
                    or caller_words.said_a_date([text]))

    def _identity_question(self):
        return self._line(caller_words.CONFIRM_IDENTITY, name=self.call.first_name)

    def _step_line(self):
        """What to say when the guard emptied a reply (a repeated greeting, a payment ask
        after hardship): the current step again."""
        call = self.call
        if call.push_stopped:
            return None if call.callback_confirmed else self._line(caller_words.CALLBACK_TIME_QUESTION)
        if call.person_status is None:
            return self._identity_question()
        if call.person_status == "customer" and not call.unlocked:
            return self._dob_question()
        if call.unlocked and not call.promise_recorded and not call.store.links_sent_in_session(call.session_id):
            due = call.get_due_details()
            if "emi_amount" in due:
                d = date.fromisoformat(due["due_date"])
                lines = (caller_words.TP_DUE_QUESTION if self._talking_to_someone_else()
                         else caller_words.DUE_QUESTION)
                return self._line(lines, amount=f"{due['emi_amount']:,}",
                                  date=f"{d.day} {d:%B}", name=call.first_name)
        return None  # the guard's usual fallback

    def _last_agent_text(self):
        for item in reversed(self.chat_ctx.items):
            if item.type == "message" and item.role == "assistant":
                return item.text_content or ""
        return ""

    def _offered_link(self):
        """Was the agent's last message about the payment link?"""
        text = self._last_agent_text().lower()
        return "link" in text or "लिंक" in text

    def _say(self, line, hang_up=False):
        """Speak a fixed line from the code; hang_up ends the call after it plays."""
        handle = self.session.say(line)
        if hang_up:
            self._end_task = asyncio.create_task(self._end_call_after(handle))
        return handle

    def on_user_state(self, state):
        """The session's user_state_changed event: "away" after USER_AWAY_SECONDS of silence."""
        if state == "speaking":
            self.silences = 0
        elif state == "away":
            self.on_user_away()

    def on_user_away(self):
        if self._end_task is not None:
            return  # already hanging up
        self.silences += 1
        if self.silences == 1:
            logger.info("RULE silence_check_in: caller quiet for %d s", USER_AWAY_SECONDS)
            # LiveKit marks "away" only once; back to "listening" so a second silence is noticed
            self.session.reset_away_timer()
            self._say(self._line(caller_words.SILENCE_CHECK))
            return
        logger.info("RULE silence_close: caller quiet twice in a row, ending the call")
        if self.call.outcome is None:
            self.call.record_outcome_or_better("no_resolution", "customer silent")
        self._say(self._line(caller_words.SILENCE_CLOSE), hang_up=True)

    async def _keep_in_history(self, message):
        ctx = self.chat_ctx.copy()
        ctx.items.append(message)
        await self.update_chat_ctx(ctx)
        self.session._conversation_item_added(message)  # the saved transcript reads this

    async def _end_call_after(self, handle):
        await handle.wait_for_playout()
        self.session.shutdown()
        try:
            get_job_context().delete_room()  # hangs up the phone call
        except RuntimeError:
            pass  # no job (text tests)

    def asked_to_call_later(self):
        """The customer asked to be called later (or said they're busy before saying who
        they are): we call them back ourselves; a "colleague" is never mentioned."""
        return (self.later.active or self.later.done or self.busy_text is not None
                or self.call.person_status == "unavailable")

    def _guard(self):
        """The ReplyGuard for the AI's next reply. "I'll try them later" is only for
        someone else. Before anyone is confirmed the fallback asks who it is; a customer
        who still owes a DOB is asked for it."""
        someone_else = self.call.person_status == "third_party"
        return ReplyGuard(self.language.current, locked=not self.call.unlocked,
                          third_party=someone_else and not self.call.unlocked,
                          locked_fallback=None if someone_else else self._step_line(),
                          greeted=True,  # the code spoke the greeting before any AI turn
                          step_fallback=self._step_line(),
                          no_push=bool(self.call.push_stopped),
                          no_callback_talk=self.call.hardship == "with_date",
                          no_colleague=self.asked_to_call_later(),
                          third_person=(self.call.first_name if self._talking_to_someone_else()
                                        else None))

    async def llm_node(self, chat_ctx, tools, model_settings: ModelSettings):
        # Every reply passes through ReplyGuard before it is spoken or saved.
        guard = self._guard()
        async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
            if isinstance(chunk, str):
                text = guard.feed(chunk)
                if text:
                    yield text
                continue
            if isinstance(chunk, llm.ChatChunk) and chunk.delta and chunk.delta.content:
                text = guard.feed(chunk.delta.content)
                chunk.delta.content = None  # tool calls and usage still go through
                if text:
                    yield text
            yield chunk
        rest = guard.finish()
        if rest:
            yield rest
        for why, sentence in guard.events:
            logger.warning("RULE speech_guard_%s: %s", why, sentence[:120])

    def _run(self, name, fn, *args):
        try:
            result = fn(*args)
        except Exception:
            logger.exception("tool %s failed", name)
            return {"error": "tool_failed", "next": "apologise_and_offer_colleague_callback"}
        logger.info("TOOL %s -> %s", name, {k: result[k] for k in LOG_KEYS if k in result})
        why = result.get("reason") or result.get("error")
        if why and any(result.get(k) is False for k in REFUSED_KEYS):
            logger.info("RULE %s_refused: %s", name, why)
        return result

    @function_tool()
    async def report_person(self, context: RunContext, status: PersonStatus) -> dict:
        """Report who answered the phone. Call this right after asking whether you are
        speaking with the customer. When it returns "give_reminder", the EMI facts are
        under "due".

        Args:
            status: "customer" if the person confirmed they are the customer; "third_party"
                if they said they are someone else or it is a wrong number; "unavailable" if
                the customer says they cannot talk right now. If you could not understand
                their answer, don't call this yet. Ask once more first.
        """
        if status == "third_party" and self.call.third_party is not None:
            return {"ok": True, "duplicate": True, "next": "continue_the_conversation"}
        was_unlocked = self.call.unlocked
        result = self._run("report_person", self.call.report_person, status)
        if result.get("next") == "confirm_identity_again":
            self._say(self._identity_question())
            raise StopResponse()
        if status == "third_party" and result.get("ok"):
            # the code runs the rest: relationship, consent, date of birth, a time to call
            last = self.call.caller_lines[-1] if self.call.caller_lines else ""
            line, hang_up = await self._flow_reply(self.flow.start(last, self.language.current))
            self._say(line, hang_up)
            raise StopResponse()
        if status == "unavailable" and result.get("ok") and not self.later.active:
            # the code asks when to call, books it and closes
            last = self.call.caller_lines[-1] if self.call.caller_lines else ""
            reply = self.later.start(last, self.language.current)
            self._say(reply.line, reply.hang_up)
            raise StopResponse()
        if result.get("next") == "give_reminder" and not was_unlocked:
            logger.info("RULE reminder_by_code: after report_person")
            line, hang_up = self._reminder()
            self._say(line, hang_up)
            raise StopResponse()
        return result

    @function_tool()
    async def get_due_details(self, context: RunContext) -> dict:
        """Get the EMI amount and due date again. report_person already returns them under
        "due", so only use this if you need them a second time."""
        return self._run("get_due_details", self.call.get_due_details)

    @function_tool()
    async def send_payment_link(self, context: RunContext, channel: Channel) -> dict:
        """Send the customer a payment link for their EMI to their registered phone number.
        You cannot choose the amount or the number.

        Args:
            channel: "sms" or "whatsapp", whichever the customer prefers.
        """
        result = self._run("send_payment_link", self.call.send_payment_link, channel)
        self._close_if_sent_after_promise(result)
        if result.get("sent") and self._talking_to_someone_else():
            logger.info("RULE link_to_customer_number: told the caller where the link went")
            self._say(self._line(caller_words.TP_LINK_SENT, name=self.call.first_name,
                                 digits=result["to_number_ending"]))
            raise StopResponse()
        return result

    def _close_if_sent_after_promise(self, result):
        if result.get("sent") and self.call.promise_recorded:
            logger.info("RULE promise_close: link sent after a promise, ending the call")
            self.call.record_outcome_or_better("promise_to_pay")
            self._say(self._promise_close(caller_words.PROMISE_CLOSE_SENT), hang_up=True)
            raise StopResponse()

    @function_tool()
    async def check_payment_status(self, context: RunContext) -> dict:
        """Check the payment records. Use it after sending a link, and whenever the customer
        says they already paid. Never believe the customer's word without this."""
        return self._run("check_payment_status", self.call.check_payment_status)

    @function_tool()
    async def record_promise_to_pay(self, context: RunContext, day: int, month: int, year: int) -> dict:
        """Save the date on which the customer promises to pay their EMI.

        Args:
            day: Day of the month, 1 to 31.
            month: Month number, 1 to 12.
            year: Full four-digit year, for example 2026.
        """
        result = self._run("record_promise_to_pay", self.call.record_promise_to_pay, day, month, year)
        if result.get("reason") == "too_far":
            # the code asks once for an earlier date, never the AI's "a colleague will call"
            d = rules.make_date(day, month, year)
            line, hang_up, _ = await self._paths(None, "", offer=single(d))
            if line:
                self._say(line, hang_up)
                raise StopResponse()
        if result.get("saved") and self.call.link_waiting:
            # the caller asked for the link before the promise was saved; send it now
            self.call.link_waiting = False
            logger.info("RULE link_after_promise: sending the link the caller asked for")
            sent = self._run("send_payment_link", self.call.send_payment_link,
                             caller_words.named_channel(self.call.caller_lines))
            self._close_if_sent_after_promise(sent)
        return result

    @function_tool()
    async def schedule_callback(self, context: RunContext, day: int, month: int, year: int,
                                hour: int, minute: int, reason: AiCallbackReason) -> dict:
        """Schedule a call back to the customer at a time they ask for.

        Args:
            day: Day of the month, 1 to 31.
            month: Month number, 1 to 12.
            year: Full four-digit year, for example 2026.
            hour: Hour on a 24-hour clock in Indian time, for example 16 for 4 pm.
            minute: Minute, 0 to 59.
            reason: "customer_unavailable" if someone else answered or the customer was away;
                "customer_requested" if the customer asked to be called later.
        """
        result = self._run("schedule_callback", self.call.schedule_callback,
                           day, month, year, hour, minute, reason)
        if result.get("saved") and self.call.push_stopped:
            logger.info("RULE handoff_close: colleague callback at %s, ending the call", result["when"])
            when = datetime.fromisoformat(result["when"])
            self._say(self._line(caller_words.HANDOFF_CLOSE, name=self.call.first_name,
                                 when=say_when(when, self.language.current)), hang_up=True)
            raise StopResponse()
        return result

    @function_tool()
    async def record_outcome(self, context: RunContext, outcome: Outcome, note: str = "") -> dict:
        """Record how the call ended. Call this once, just before saying goodbye.

        Args:
            outcome: The label that best matches how the call ended.
            note: A few words of detail, never personal data.
        """
        result = self._run("record_outcome", self.call.record_outcome, outcome, note)
        if result.get("saved") and self.call.promise_recorded:
            # the AI is about to say goodbye: the code says the promise close and hangs up
            logger.info("RULE promise_close: outcome recorded after a promise, ending the call")
            lines = (caller_words.PROMISE_CLOSE_SENT if self.call.link_sent_after_promise
                     else caller_words.PROMISE_CLOSE_DECLINED)
            self._say(self._promise_close(lines), hang_up=True)
            raise StopResponse()
        return result

    @function_tool()
    async def transfer_to_human(self, context: RunContext, reason: HandoffReason) -> dict:
        """Ask for a human colleague to call the customer back. This does not connect the
        call to anyone.

        Args:
            reason: Why. "customer_asked_for_person" only if the customer asked for one.
                After a promise is saved, only legal_question, hardship, dispute or
                customer_asked_for_person are accepted.
        """
        return self._run("transfer_to_human", self.call.transfer_to_human, reason)


def call_note(call, customer, kind, today):
    """The "# This call" section added to the prompt: the facts for this one call."""
    language = "Hindi" if call.language == "hi" else "English"
    what = ("a follow-up to a promise the customer made earlier"
            if kind == "followup" else "a first reminder")
    loan = customer["loan"]
    due = date.fromisoformat(loan["due_date"])
    upcoming = ", ".join(
        f"{d:%A} {d.day} {d:%B}"
        for d in (today.date() + timedelta(days=i) for i in range(1, 8)))
    return (f"\n\n# This call\nCustomer first name: {call.first_name}\n"
            f"Today: {today:%A} {today.day} {today:%B %Y} (IST)\n"
            f"Start in: {language}\nCall type: {what}\n"
            f"EMI amount due: ₹{loan['emi_amount']:,}\n"
            f"EMI was due on: {due.day} {due:%B}\n"
            f"Next 7 days: {upcoming}\n"
            "When the customer names a day (a weekday, aaj, kal, parson), the code adds its "
            "date to that turn. Use that date and confirm it; don't work it out yourself.\n"
            "Write every amount and date in digits exactly as shown here, like ₹2,200 and "
            "30 October. Never write numbers as words. Never invent or round a number. "
            "Never say the year.\n")


async def entrypoint(ctx: agents.JobContext):
    logger.info("database host: %s", urlparse(config.DATABASE_URL).hostname)
    logger.info("STT_LANGUAGE=%s LLM_MODEL=%s", config.STT_LANGUAGE, config.LLM_MODEL)
    session_id = uuid.uuid4()
    started_at = datetime.now(timezone.utc)
    customer_id, kind = pick_target(ctx)

    gate = schema_gate(await check_schema(), config.TEST_MODE)
    if gate == "refuse":
        logger.error("refusing the call: the database is missing columns the code needs "
                     "(see SCHEMA MISMATCH above); run the pending migrations")
        return
    if gate == "memory":
        logger.warning("TEST_MODE is on: using in-memory data because of the SCHEMA MISMATCH; "
                       "nothing from this call is saved to the database")
        store, persistent = MemoryStore.from_files(clock=rules.now_ist), False
    else:
        store, persistent = await build_store(customer_id)
    if store is None:
        logger.error("database unavailable: not placing the call")
        return

    customer = store.get_customer(customer_id)
    if customer is None:
        logger.error("unknown customer %s: not starting the call", customer_id)
        return

    # Safety gate: the same check the scheduler uses, run again before we speak.
    attempts = [] if config.TEST_MODE else store.attempt_times(customer_id)
    verdict = rules.can_call_now(customer, attempts, rules.now_ist())
    window_only = config.TEST_MODE and verdict["reason"] == "outside_window"
    if not verdict["allowed"] and not window_only:
        logger.warning("call blocked before speaking: %s", verdict["reason"])
        return
    if config.TEST_MODE:
        logger.warning("TEST_MODE is on: calling hours and attempt limits are skipped, "
                       "and this call is not counted as an attempt")
    else:
        store.add_attempt(customer_id, rules.now_ist(), str(session_id), kind,
                          phone=customer.get("phone"))  # its hash: a later change lifts a wrong-number hold

    call = Call(store, customer_id, session_id=str(session_id), clock=rules.now_ist)

    note = call_note(call, customer, kind, rules.now_ist())

    session = AgentSession(
        stt=deepgram.STT(model=config.STT_MODEL, language=config.STT_LANGUAGE),
        llm=build_llm(),
        tts=build_tts(),
        vad=silero.VAD.load(),
        turn_handling={"endpointing": {"min_delay": config.ENDPOINT_MIN_DELAY,
                                       "max_delay": config.ENDPOINT_MAX_DELAY}},
        user_away_timeout=USER_AWAY_SECONDS,
    )

    @session.on("metrics_collected")
    def _on_metrics(ev: MetricsCollectedEvent):
        metrics.log_metrics(ev.metrics)

    @session.on("agent_state_changed")
    def _on_agent_state(ev):
        # Customer talked over the agent: the dialer must drop audio it hasn't played yet
        if ev.old_state == "speaking" and session.user_state == "speaking":
            asyncio.create_task(ctx.room.local_participant.publish_data(
                json.dumps({"type": "clear"}).encode(), topic="sampark", reliable=True))

    caller_joined = False  # set once the caller is in the room; finish() reads it

    async def finish():
        ended_at = datetime.now(timezone.utc)
        if not caller_joined and call.outcome is None:
            # Timed out, or the room was deleted first (the bridge drops unused rooms):
            # either way the job ends here, so this is the one place that records it
            call.outcome, call.note = NO_CALLER, "caller never joined the room"
            logger.warning("RULE no_caller: outcome %s", NO_CALLER)
        if call.finalize_outcome():
            logger.warning("RULE outcome_filled_by_code: %s", call.outcome)
        store.set_attempt_outcome(str(session_id), call.outcome)
        if persistent:
            try:
                saved = await store.flush()
                logger.info("saved changes: %s", {k: v for k, v in saved.items() if v})
            except Exception:
                logger.exception("could not save call changes, writing JSON fallback")
                TRANSCRIPT_DIR.mkdir(exist_ok=True)
                (TRANSCRIPT_DIR / f"{session_id}_changes.json").write_text(
                    json.dumps(store.changes(), default=str, ensure_ascii=False, indent=2),
                    encoding="utf-8")
        turns = db.extract_turns(session.history)
        if not turns and caller_joined:
            return  # a call nobody spoke in; a no-caller call is still saved, for its outcome
        if gate == "memory":  # the database doesn't match the code: transcript to a file
            TRANSCRIPT_DIR.mkdir(exist_ok=True)
            payload = [{**t, "created_at": t["created_at"].isoformat()} for t in turns]
            (TRANSCRIPT_DIR / f"{session_id}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info("saved call %s to %s (schema mismatch)", session_id, TRANSCRIPT_DIR)
            return
        try:
            await db.save_call(session_id, ctx.room.name, "agent", started_at, ended_at,
                               turns, outcome=call.outcome, metadata=call.summary())
            logger.info("saved call %s (%d turns), outcome=%s", session_id, len(turns), call.outcome)
        except Exception:
            logger.exception("DB save failed, writing JSON fallback")
            TRANSCRIPT_DIR.mkdir(exist_ok=True)
            payload = [{**t, "created_at": t["created_at"].isoformat()} for t in turns]
            (TRANSCRIPT_DIR / f"{session_id}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # Save as soon as the customer hangs up, not when the process exits. The three
    # triggers share one task, so the store is flushed once.
    saving = None

    def save_now(why):
        nonlocal saving
        if saving is None:
            logger.info("RULE save_on_end: %s, saving the call now", why)
            saving = asyncio.ensure_future(finish())
        return saving

    @ctx.room.on("participant_disconnected")
    def _on_left(participant: rtc.RemoteParticipant):
        if participant.kind != rtc.ParticipantKind.PARTICIPANT_KIND_AGENT:
            save_now("customer disconnected")

    @session.on("close")
    def _on_close(ev):
        save_now("session closed")

    async def _on_shutdown():
        await save_now("process shutting down")

    ctx.add_shutdown_callback(_on_shutdown)

    agent = Collector(call, BASE_PROMPT + note)

    @session.on("user_state_changed")
    def _on_user_state(ev):
        agent.on_user_state(ev.new_state)

    await session.start(
        room=ctx.room,
        agent=agent,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=noise_cancellation.BVC(),
            ),
        ),
    )
    if not await wait_for_caller(ctx):
        logger.warning("nobody joined within %ss, ending without a greeting", CALLER_WAIT_SECONDS)
        ctx.shutdown("caller never joined")  # finish() runs on shutdown and saves the call
        return
    caller_joined = True
    if call.language == "hi":
        greeting = (f"नमस्ते, मैं {config.COMPANY_NAME} की AI असिस्टेंट बोल रही हूँ। "
                    f"यह कॉल रिकॉर्ड हो सकती है। क्या मेरी बात {call.first_name} जी से हो रही है?")
    else:
        greeting = (f"Hello, this is the AI assistant from {config.COMPANY_NAME}. "
                    f"This call may be recorded. Am I speaking with {call.first_name}?")
    await session.say(greeting)


TAKES_CALLS = {"start", "dev", "console", "connect"}  # not "download-files" (Docker build)


def startup_schema_check(argv):
    """Before the worker starts: with TEST_MODE off, a database that is missing columns
    means no worker at all (exit code 1), so no call is ever taken. Returns the exit code
    or None to carry on."""
    if len(argv) < 2 or argv[1] not in TAKES_CALLS:
        return None
    ok = asyncio.run(check_schema())  # logs "SCHEMA MISMATCH: ..." at ERROR
    if ok is False:
        if not config.TEST_MODE:
            logger.error("refusing to take calls: run the pending migrations "
                         "(README, Deploying) and start the agent again")
            return 1
        logger.error("TEST_MODE is on: calls will use in-memory data, and the mismatch is "
                     "logged on every call")
    return None


if __name__ == "__main__":
    code = startup_schema_check(sys.argv)
    if code is not None:
        sys.exit(code)
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))