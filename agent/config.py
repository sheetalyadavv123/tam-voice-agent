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
TTS_MODEL = os.getenv("TTS_MODEL", "eleven_flash_v2_5")
TTS_VOICE_ID = os.getenv("TTS_VOICE_ID", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "")
COMPANY_NAME = os.getenv("COMPANY_NAME", "Tam Infosoft")
TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"