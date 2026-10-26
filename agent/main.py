import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Let this file import the "tools" folder in the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from livekit import agents
from livekit.agents import Agent, AgentSession, RunContext, function_tool
from livekit.plugins import deepgram, elevenlabs, google, openai, silero

import config
import db
from tools import loan_tools

logger = logging.getLogger("tam-voice-agent")
PROMPT = (Path(__file__).parent.parent / "prompts" / "system.md").read_text(encoding="utf-8")
TRANSCRIPT_DIR = Path(__file__).parent.parent / "transcripts"


def build_llm():
    if config.LLM_PROVIDER == "openai":
        return openai.LLM(model=config.LLM_MODEL)
    return google.LLM(model=config.LLM_MODEL)


class Assistant(Agent):
    def __init__(self, call: loan_tools.Session, call_note: str):
        super().__init__(instructions=PROMPT + call_note)
        self.call = call  # this call's notepad (customer, verified, attempts)

    @function_tool()
    async def verify_identity(self, context: RunContext, date_of_birth: str) -> dict:
        """Verify the caller's identity. Pass the date of birth the caller said,
        converted to YYYY-MM-DD."""
        result = loan_tools.verify_identity(self.call, date_of_birth)
        logger.info("TOOL verify_identity -> %s", result)
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
        tts=elevenlabs.TTS(model=config.TTS_MODEL, voice_id=config.TTS_VOICE_ID),
        vad=silero.VAD.load(),
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