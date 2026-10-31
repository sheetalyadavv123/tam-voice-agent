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
from tools import caller_words, dates, rules
from tools.collection_tools import (
    AiCallbackReason, Call, Channel, HandoffReason, Outcome, PersonStatus,
)
from tools.language import LanguageTracker, turn_language
from tools.speech_guard import ReplyGuard
from tools.store import MemoryStore
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


def pick_target(ctx):
    """Which customer is this call for? Job metadata wins; otherwise the test setting."""
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
        self.silences = 0  # times in a row the caller went quiet
        self._end_task = None

    async def on_user_turn_completed(self, turn_ctx, new_message):
        text = new_message.text_content or ""
        self.call.heard(text)
        self.silences = 0
        agreed = self.call.caller_agreed_to(self._last_agent_text(), text)
        if agreed:
            logger.info("RULE promise_date_agreed: %s (link waits for record_promise_to_pay)", agreed)
        line, hang_up = self._fixed_reply(text)
        if line:
            # LiveKit drops a stopped turn's message; keep it so the transcript has it
            await self._keep_in_history(new_message)
            self._say(line, hang_up)
            raise StopResponse()
        if (turn_ctx is not None and self.call.person_status is None
                and caller_words.confirms_identity(text, self.call.first_name)):
            # nano sometimes answers a "haan ji" without reporting it; the tool still checks
            turn_ctx.add_message(role="system", content=(
                f"The caller just confirmed they are {self.call.first_name}. "
                "Call report_person with status customer now."))
        today = self.call.clock().date()
        days = dates.note_for(text, today)
        if days and turn_ctx is not None:
            logger.info("RULE weekday_resolved: %s", days)
            turn_ctx.add_message(role="system", content=(
                f"The caller named these days (IST): {days}. Use exactly these dates."))
            promise = self.call.promise
            new = [d for _, d in dates.days_named(text, today)]
            if promise and promise["date"] not in new:
                logger.info("RULE promise_date_change: caller named %s, promise is %s",
                            new[-1], promise["date"])
                turn_ctx.add_message(role="system", content=(
                    f"The saved promise is {dates.say(promise['date'])}, but the caller named "
                    f"{dates.say(new[-1])}. If they are changing the promise date, call "
                    "record_promise_to_pay with the new date; it replaces the old one."))
        switched = self.language.observe(text)
        if not switched:
            if turn_language(text) not in (None, self.language.current):
                logger.info("RULE language_hold: staying in %s for %r", self.language.current, text[:60])
            return
        name = "English" if switched == "en" else "Hindi"
        logger.info("RULE language_switch: now %s after %r", name, text[:60])
        rule = (f"# Language now\nThe customer is speaking {name}. "
                f"Reply only in {name} from now on.")
        # update_instructions covers later turns; turn_ctx is this reply's copy
        await self.update_instructions(self.base_instructions + "\n" + rule + "\n")
        turn_ctx.add_message(role="system", content=rule)
        if isinstance(self.session.tts, sarvam.TTS):
            self.session.tts.update_options(
                target_language_code="en-IN" if switched == "en" else config.SARVAM_LANGUAGE)

    def _fixed_reply(self, text):
        """Some questions get a fixed answer from the code and the AI doesn't get the turn.
        Returns (line, hang_up); line is None to let the AI reply."""
        lang = self.language.current
        if self.call.unlocked and caller_words.legal_question(text):
            logger.warning("RULE legal_handoff: %s", text[:120])
            self.call.transfer_to_human("legal_question")
            self.call.record_outcome("human_callback", "legal question")
            return caller_words.line(caller_words.LEGAL_LINE, lang), True
        if (self.call.promise_recorded and not self.call.link_sent_after_promise
                and self._offered_link() and caller_words.declines(text)
                and not self._names_a_day(text)):  # "nahi, kal" changes the date, not the link
            logger.info("RULE promise_close: link declined after a promise (%r)", text[:60])
            self.call.record_outcome_or_better("promise_to_pay")
            return self._promise_close(caller_words.PROMISE_CLOSE_DECLINED), True
        if self.call.person_status in (None, "third_party") and caller_words.asks_why_calling(text):
            if self.call.person_status is None:
                if not caller_words.says_not_the_customer(text):
                    return None, False  # maybe the customer themselves; the AI confirms who
                self.call.report_person("third_party")
            logger.info("RULE third_party_line: %s", text[:120])
            name = self.call.first_name
            return caller_words.line(caller_words.THIRD_PARTY_LINE, lang, name=name), False
        return None, False

    def _line(self, lines, **values):
        return caller_words.line(lines, self.language.current, **values)

    def _promise_close(self, lines):
        p = self.call.promise
        return self._line(lines, name=self.call.first_name, amount=f"{p['amount']:,}",
                          date=f"{p['date'].day} {p['date']:%B}")

    def _dob_question(self):
        return self._line(caller_words.DOB_QUESTION, name=self.call.first_name)

    def _names_a_day(self, text):
        return bool(dates.days_named(text, self.call.clock().date())
                    or caller_words.said_a_date([text]))

    def _identity_question(self):
        return self._line(caller_words.CONFIRM_IDENTITY, name=self.call.first_name)

    def _step_line(self):
        """What to say when a reply was only a repeated greeting: the current step again."""
        call = self.call
        if call.person_status is None:
            return self._identity_question()
        if call.person_status == "customer" and not call.unlocked:
            return self._dob_question()
        if call.unlocked and not call.promise_recorded and not call.store.links_sent_in_session(call.session_id):
            due = call.get_due_details()
            if "emi_amount" in due:
                d = date.fromisoformat(due["due_date"])
                return self._line(caller_words.DUE_QUESTION, amount=f"{due['emi_amount']:,}",
                                  date=f"{d.day} {d:%B}")
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

    async def llm_node(self, chat_ctx, tools, model_settings: ModelSettings):
        # Every reply passes through ReplyGuard before it is spoken or saved.
        # "I'll try them later" is only for someone else. Before anyone is confirmed the
        # fallback asks who it is; a customer who still owes a DOB is asked for it.
        guard = ReplyGuard(self.language.current, locked=not self.call.unlocked,
                           third_party=self.call.person_status == "third_party",
                           locked_fallback=(None if self.call.person_status == "third_party"
                                            else self._step_line()),
                           greeted=True,  # the code spoke the greeting before any AI turn
                           step_fallback=self._step_line())
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
        result = self._run("report_person", self.call.report_person, status)
        if result.get("next") == "confirm_identity_again":
            self._say(self._identity_question())
            raise StopResponse()
        if result.get("next") == "ask_date_of_birth":
            logger.info("RULE dob_question: spoken by code after report_person")
            self._say(self._dob_question())
            raise StopResponse()
        return result

    @function_tool()
    async def verify_identity(self, context: RunContext, day: int, month: int, year: int) -> dict:
        """Check the customer's date of birth. Only use this if a tool result tells you to
        ask for it.

        Args:
            day: Day of the month, 1 to 31.
            month: Month number, 1 to 12.
            year: Full four-digit year, for example 1985. Ask again if only two digits.
        """
        result = self._run("verify_identity", self.call.verify_identity, day, month, year)
        if result.get("locked"):
            logger.info("RULE dob_failed_close: too many wrong dates of birth, ending the call")
            self.call.transfer_to_human("identity_check_failed")
            self.call.record_outcome_or_better("human_callback", "identity check failed")
            self._say(self._line(caller_words.DOB_FAILED_CLOSE), hang_up=True)
            raise StopResponse()
        if result.get("next") == "ask_for_date_of_birth":
            logger.info("RULE dob_question: caller has not said a date yet")
            self._say(self._dob_question())
            raise StopResponse()
        if result.get("verified") is False and (
                "attempts_left" in result or result.get("error") == "impossible_date"):
            logger.info("RULE dob_retry: date of birth did not match, asking again")
            self._say(self._line(caller_words.DOB_RETRY, name=self.call.first_name))
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
        return self._run("schedule_callback", self.call.schedule_callback,
                         day, month, year, hour, minute, reason)

    @function_tool()
    async def record_outcome(self, context: RunContext, outcome: Outcome, note: str = "") -> dict:
        """Record how the call ended. Call this once, just before saying goodbye.

        Args:
            outcome: The label that best matches how the call ended.
            note: A few words of detail, never personal data.
        """
        return self._run("record_outcome", self.call.record_outcome, outcome, note)

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
    session_id = uuid.uuid4()
    started_at = datetime.now(timezone.utc)
    customer_id, kind = pick_target(ctx)

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
        store.add_attempt(customer_id, rules.now_ist(), str(session_id), kind)

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

    async def finish():
        ended_at = datetime.now(timezone.utc)
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
        if not turns:
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
    if call.language == "hi":
        greeting = (f"नमस्ते, मैं {config.COMPANY_NAME} की AI असिस्टेंट बोल रही हूँ। "
                    f"यह कॉल रिकॉर्ड हो सकती है। क्या मेरी बात {call.first_name} जी से हो रही है?")
    else:
        greeting = (f"Hello, this is the AI assistant from {config.COMPANY_NAME}. "
                    f"This call may be recorded. Am I speaking with {call.first_name}?")
    await session.say(greeting)


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))