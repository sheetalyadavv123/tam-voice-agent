from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
skip = ("realtime", "audio", "transcribe", "tts", "image", "codex", "embedding", "moderation")
for m in sorted(x.id for x in OpenAI().models.list()):
    if any(k in m for k in ("mini", "nano", "gpt-5", "gpt-6", "4.1")) and not any(s in m for s in skip):
        print(m)