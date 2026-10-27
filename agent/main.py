import json
import logging
import os
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

# Let this file import the "tools" folder in the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from livekit import agents
from livekit.agents import Agent, AgentSession, RunContext, function_tool
from livekit.plugins import deepgram, elevenlabs, google, openai, sarvam, silero

import config
import db
from tools import loan_tools

logger = logging.getLogger("tam-voice-agent")
PROMPT = (Path(__file__).parent.parent / "prompts" / "system.md").read_text(encoding="utf-8")
TRANSCRIPT_DIR = Path(__file__).parent.parent / "transcripts"


def build_llm():
    if config.LLM_PROVIDER == "openai":
        extra = {}
        if config.LLM_REASONING_EFFORT:
            extra["reasoning_effort"] = config.LLM_REASONING_EFFORT
        return openai.LLM(model=config.LLM_MODEL, **extra)
    return google.LLM(model=config.LLM_MODEL)


def build_tts():
    if config.TTS_PROVIDER == "sarvam":
        return sarvam.TTS(target_language_code=config.SARVAM_LANGUAGE, model="bulbul:v3",
                          speaker=config.SARVAM_SPEAKER)
    return elevenlabs.TTS(model=config.TTS_MODEL, voice_id=config.TTS_VOICE_ID)


class Assistant(Agent):
    def __init__(self, call: loan_tools.Session, call_note: str):
        super().__init__(instructions=PROMPT + call_note)
        self.call = call  # this call's notepad (customer, verified, attempts)

    @function_tool()
    async def verify_identity(self, context: RunContext, day: int, month: int, year: int) -> dict:
        """Verify the caller's identity using the date of birth they said.
        If verified, the result also includes the caller's loan details, so you
        do not need to call get_loan_status afterwards.

        Args:
            day: Day of the month, 1-31. For "second November nineteen eighty five" this is 2.
            month: Month number, 1-12. November is 11.
            year: Full four-digit year. "nineteen eighty five" is 1985.
        """
        # Build the date in code: small LLMs often mis-format date strings.
        try:
            dob = date(year, month, day).isoformat()
        except ValueError:
            logger.info("TOOL verify_identity invalid date day=%s month=%s year=%s", day, month, year)
            return {"verified": False, "error": "invalid_date", "hint": "Ask the caller to repeat it."}
        # Filler so the caller isn't left in silence while the next LLM turn runs
        context.session.say("एक moment, मैं check करती हूँ।", add_to_chat_ctx=False)
        result = loan_tools.verify_identity(self.call, dob)
        logger.info("TOOL verify_identity(%s) -> %s", dob, result)
        if result.get("verified"):
            # Return the loan details now to save a second tool round trip
            result["loan"] = loan_tools.get_loan_status(self.call)
        return result

    @function_tool()
    async def get_loan_status(self, context: RunContext) -> dict:
        """Get the verified caller's loan details. Only works after the caller
        has been verified."""
        result = loan_tools.get_loan_status(self.call)
        logger.info("TOOL get_loan_status -> %s", "error" if "error" in result else "ok")
        return result

    @function_tool()
    async def record_outcome(self, context: RunContext, outcome: str, note: str = "") -> dict:
        """Record how the call ended. outcome must be one of: paid_confirmed,
        promise_to_pay, dispute, callback_requested, wrong_person, transferred,
        no_resolution."""
        result = loan_tools.record_outcome(self.call, outcome, note)
        logger.info("TOOL record_outcome(%s) -> %s", outcome, result)
        return result

    @function_tool()
    async def transfer_to_human(self, context: RunContext, reason: str) -> dict:
        """Transfer the caller to a human agent. Give a short reason."""
        result = loan_tools.transfer_to_human(self.call, reason)
        logger.info("TOOL transfer_to_human -> %s", result)
        return result


async def entrypoint(ctx: agents.JobContext):
    session_id = uuid.uuid4()
    started_at = datetime.now(timezone.utc)

    # One notepad per call. The lookup happens in code, before the AI speaks.
    call = loan_tools.Session()
    caller_phone = os.getenv("TEST_CALLER_PHONE", "")
    found = loan_tools.find_customer(call, caller_phone)["found"] if caller_phone else False
    logger.info("caller lookup: found=%s", found)

    if found:
        call_note = ("\n\n# This call\nThe caller's number matched an account. "
                     "Ask for their date of birth to verify them before sharing anything.")
    else:
        call_note = ("\n\n# This call\nThe caller's number was NOT found in our records. "
                     "Do not share any account information. Offer to connect a human agent.")

    session = AgentSession(
        stt=deepgram.STT(model=config.STT_MODEL, language=config.STT_LANGUAGE),
        llm=build_llm(),
        tts=build_tts(),
        vad=silero.VAD.load(),
        preemptive_generation=True,  # start the LLM before end-of-turn is confirmed
    )

    async def save_transcript():
        ended_at = datetime.now(timezone.utc)
        turns = db.extract_turns(session.history)
        if not turns:
            return
        meta = {"customer_id": call.customer_id, "verified": call.verified,
                "verification_attempts": call.attempts, "note": call.note}
        try:
            await db.save_call(session_id, ctx.room.name, "console", started_at,
                               ended_at, turns, outcome=call.outcome, metadata=meta)
            logger.info("saved call %s (%d turns)", session_id, len(turns))
        except Exception:
            logger.exception("DB save failed, writing JSON fallback")
            TRANSCRIPT_DIR.mkdir(exist_ok=True)
            payload = [{**t, "created_at": t["created_at"].isoformat()} for t in turns]
            (TRANSCRIPT_DIR / f"{session_id}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    ctx.add_shutdown_callback(save_transcript)

    await session.start(room=ctx.room, agent=Assistant(call, call_note))
    await session.generate_reply(
        instructions="Greet the caller in Hindi in one short sentence, say you are "
                     "Tam Infosoft's AI assistant, and ask how you can help."
    )


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))