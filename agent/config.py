import os
from dotenv import load_dotenv

load_dotenv()

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "google")  # google | openai
LLM_MODEL = os.getenv("LLM_MODEL", "")
STT_MODEL = os.getenv("STT_MODEL", "nova-3")
STT_LANGUAGE = os.getenv("STT_LANGUAGE", "multi")
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "sarvam")  # sarvam | elevenlabs
SARVAM_LANGUAGE = os.getenv("SARVAM_LANGUAGE", "hi-IN")
SARVAM_SPEAKER = os.getenv("SARVAM_SPEAKER", "simran")
# Chosen by listening to phone-quality clips from scripts/phone_sim.py
TTS_PACE = float(os.getenv("TTS_PACE") or "0.9")
if not 0.5 <= TTS_PACE <= 2.0:
    raise ValueError(f"TTS_PACE must be between 0.5 and 2.0, got {TTS_PACE}")
TTS_MODEL = os.getenv("TTS_MODEL", "eleven_flash_v2_5")
TTS_VOICE_ID = os.getenv("TTS_VOICE_ID", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "")
COMPANY_NAME = os.getenv("COMPANY_NAME", "Tam Infosoft")
TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"
APP_ENV = os.getenv("APP_ENV", "")
# Seconds of silence before the agent replies. LiveKit's defaults are 0.5 and 3.0; the
# 3 s ceiling made short answers like "हाँ" wait about 2.5 s.
ENDPOINT_MIN_DELAY = float(os.getenv("ENDPOINT_MIN_DELAY", "0.4"))
ENDPOINT_MAX_DELAY = float(os.getenv("ENDPOINT_MAX_DELAY", "1.5"))
