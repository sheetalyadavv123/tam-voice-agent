import os
from dotenv import load_dotenv

load_dotenv()

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "google")  # google | openai
LLM_MODEL = os.getenv("LLM_MODEL", "")
STT_MODEL = os.getenv("STT_MODEL", "nova-3")
STT_LANGUAGE = os.getenv("STT_LANGUAGE", "multi")
TTS_MODEL = os.getenv("TTS_MODEL", "eleven_flash_v2_5")
TTS_VOICE_ID = os.getenv("TTS_VOICE_ID", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")