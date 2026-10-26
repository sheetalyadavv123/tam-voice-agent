import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from livekit import agents
from livekit.agents import Agent, AgentSession
from livekit.plugins import deepgram, elevenlabs, google, openai, silero

import config
import db

logger = logging.getLogger("tam-voice-agent")
PROMPT = (Path(__file__).parent.parent / "prompts" / "system.md").read_text(encoding="utf-8")
TRANSCRIPT_DIR = Path(__file__).parent.parent / "transcripts"


def build_llm():
    if config.LLM_PROVIDER == "openai":
        return openai.LLM(model=config.LLM_MODEL)
    return google.LLM(model=config.LLM_MODEL)


class Assistant(Agent):
    def __init__(self):
        super().__init__(instructions=PROMPT)


async def entrypoint(ctx: agents.JobContext):
    session_id = uuid.uuid4()
    started_at = datetime.now(timezone.utc)

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
        try:
            await db.save_call(session_id, ctx.room.name, "console",
                               started_at, ended_at, turns)
            logger.info("saved call %s (%d turns)", session_id, len(turns))
        except Exception:
            logger.exception("DB save failed, writing JSON fallback")
            TRANSCRIPT_DIR.mkdir(exist_ok=True)
            payload = [{**t, "created_at": t["created_at"].isoformat()} for t in turns]
            (TRANSCRIPT_DIR / f"{session_id}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    ctx.add_shutdown_callback(save_transcript)

    await session.start(room=ctx.room, agent=Assistant())
    await session.generate_reply(instructions="Greet the user in one short sentence.")


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))