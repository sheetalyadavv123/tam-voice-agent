import os, sys, time
from dotenv import load_dotenv
from google import genai

load_dotenv()
client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
models = sys.argv[1:] or [os.environ["LLM_MODEL"]]

for m in models:
    times = []
    for _ in range(3):
        t0 = time.perf_counter()
        try:
            stream = client.models.generate_content_stream(
                model=m, contents="Reply in one short Hindi sentence: say hello.")
            for chunk in stream:
                if chunk.text:
                    times.append(time.perf_counter() - t0)
                    break
        except Exception as e:
            print(m, "ERROR", str(e)[:120])
            break
    if times:
        print(f"{m}: first token " + ", ".join(f"{t:.2f}s" for t in times))