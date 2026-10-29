import json
import logging
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal  # noqa: F401  (kept for tool signatures)

# Let this file import the "tools" folder in the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from livekit import agents
from livekit.agents import (
    Agent, AgentSession, MetricsCollectedEvent, ModelSettings, RunContext, function_tool, llm,
    metrics, room_io,
)
from livekit.plugins import (
    deepgram, elevenlabs, google, noise_cancellation, openai, sarvam, silero,
)

import config
import db
from tools import rules
from tools.collection_tools import AiCallbackReason, Call, Channel, Outcome, PersonStatus
from tools.language import LanguageTracker
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


def build_llm():
    if config.LLM_PROVIDER == "openai":
        extra = {}
        if config.LLM_REASONING_EFFORT:
            extra["reasoning_effort"] = config.LLM_REASONING_EFFORT
        return openai.LLM(model=config.LLM_MODEL, **extra)
    return google.LLM(model=config.LLM_MODEL)


def build_tts():
    if config.TTS_PROVIDER == "sarvam":
        return sarvam.TTS(
            target_language_code=config.SARVAM_LANGUAGE,
            model="bulbul:v3",
            speaker=config.SARVAM_SPEAKER,
        )
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

    async def on_user_turn_completed(self, turn_ctx, new_message):
        switched = self.language.observe(new_message.text_content or "")
        if not switched:
            return
        name = "English" if switched == "en" else "Hindi"
        logger.info("customer is speaking %s, switching language", name)
        rule = (f"# Language now\nThe customer is speaking {name}. "
                f"Reply only in {name} from now on.")
        # update_instructions covers later turns; turn_ctx is this reply's copy
        await self.update_instructions(self.base_instructions + "\n" + rule + "\n")
        turn_ctx.add_message(role="system", content=rule)
        if isinstance(self.session.tts, sarvam.TTS):
            self.session.tts.update_options(
                target_language_code="en-IN" if switched == "en" else config.SARVAM_LANGUAGE)

    async def llm_node(self, chat_ctx, tools, model_settings: ModelSettings):
        # Every reply passes through ReplyGuard before it is spoken or saved.
        guard = ReplyGuard(self.language.current, locked=not self.call.unlocked)
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
            logger.warning("SPEECH GUARD %s: %s", why, sentence[:120])

    def _run(self, name, fn, *args):
        try:
            result = fn(*args)
        except Exception:
            logger.exception("tool %s failed", name)
            return {"error": "tool_failed", "next": "apologise_and_offer_colleague_callback"}
        logger.info("TOOL %s -> %s", name, {k: result[k] for k in LOG_KEYS if k in result})
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
        return self._run("report_person", self.call.report_person, status)

    @function_tool()
    async def verify_identity(self, context: RunContext, day: int, month: int, year: int) -> dict:
        """Check the customer's date of birth. Only use this if a tool result tells you to
        ask for it.

        Args:
            day: Day of the month, 1 to 31.
            month: Month number, 1 to 12.
            year: Full four-digit year, for example 1985. Ask again if only two digits.
        """
        return self._run("verify_identity", self.call.verify_identity, day, month, year)

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
        return self._run("send_payment_link", self.call.send_payment_link, channel)

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
        return self._run("record_promise_to_pay", self.call.record_promise_to_pay, day, month, year)

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
    async def transfer_to_human(self, context: RunContext, reason: str) -> dict:
        """Ask for a human colleague to call the customer back. This does not connect the
        call to anyone.

        Args:
            reason: A short reason, for example "customer reports hardship".
        """
        return self._run("transfer_to_human", self.call.transfer_to_human, reason)


async def entrypoint(ctx: agents.JobContext):
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

    language = "Hindi" if call.language == "hi" else "English"
    what = ("a follow-up to a promise the customer made earlier"
            if kind == "followup" else "a first reminder")
    today = rules.now_ist()
    loan = customer["loan"]
    due = date.fromisoformat(loan["due_date"])
    upcoming = ", ".join(
        f"{d:%A} {d.day} {d:%B}"
        for d in (today.date() + timedelta(days=i) for i in range(1, 8)))
    note = (f"\n\n# This call\nCustomer first name: {call.first_name}\n"
            f"Today: {today:%A} {today.day} {today:%B %Y} (IST)\n"
            f"Start in: {language}\nCall type: {what}\n"
            f"EMI amount due: ₹{loan['emi_amount']:,}\n"
            f"EMI was due on: {due.day} {due:%B %Y}\n"
            f"Next 7 days: {upcoming}\n"
            "If the customer names a weekday, take its date from 'Next 7 days' and confirm it. "
            "Don't ask them for the date.\n"
            "Write every amount and date in digits exactly as shown here, like ₹2,200 and "
            "30 October. Never write numbers as words. Never invent or round a number.\n")

    session = AgentSession(
        stt=deepgram.STT(model=config.STT_MODEL, language=config.STT_LANGUAGE),
        llm=build_llm(),
        tts=build_tts(),
        vad=silero.VAD.load(),
    )

    @session.on("metrics_collected")
    def _on_metrics(ev: MetricsCollectedEvent):
        metrics.log_metrics(ev.metrics)

    async def finish():
        ended_at = datetime.now(timezone.utc)
        if call.finalize_outcome():
            logger.warning("no outcome was recorded, code set %s", call.outcome)
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

    ctx.add_shutdown_callback(finish)

    await session.start(
        room=ctx.room,
        agent=Collector(call, BASE_PROMPT + note),
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