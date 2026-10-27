import os, sys, time
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")
client = OpenAI()
PROMPT = ("Reply in one short Hindi sentence: your EMI of four thousand five "
          "hundred rupees is due on 28 October.")
effort = os.getenv("LLM_REASONING_EFFORT") or None

for model in sys.argv[1:]:
    for _ in range(3):
        t0, first, text = time.perf_counter(), None, ""
        try:
            extra = {"reasoning_effort": effort} if effort else {}
            stream = client.chat.completions.create(
                model=model, stream=True,
                messages=[{"role": "user", "content": PROMPT}], **extra)
            for ch in stream:
                d = ch.choices[0].delta.content if ch.choices else None
                if d:
                    first = first or time.perf_counter() - t0
                    text += d
            print(f"{model}: first token {first:.2f}s | {text.strip()[:80]}")
        except Exception as e:
            print(f"{model}: ERROR {str(e)[:120]}")
            break
        time.sleep(2)