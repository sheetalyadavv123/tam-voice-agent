import os, requests
from dotenv import load_dotenv

load_dotenv()
r = requests.post(
    f"https://api.elevenlabs.io/v1/text-to-speech/{os.environ['TTS_VOICE_ID']}",
    headers={"xi-api-key": os.environ["ELEVEN_API_KEY"]},
    json={"text": "नमस्ते, आपकी ईएमआई चार हज़ार पाँच सौ रुपये है।",
          "model_id": os.environ["TTS_MODEL"]},
)
print(r.status_code)
if r.status_code == 200:
    open("tts_test.mp3", "wb").write(r.content)
    print("Saved tts_test.mp3, play it")
else:
    print(r.text[:300])