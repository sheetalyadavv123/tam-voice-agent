import os, requests
from dotenv import load_dotenv

load_dotenv()

VOICES = {
    "sarah": "EXAVITQu4vr4xnSDxMaL",
    "alice": "Xb7hH8MSUJpSbSDYk0k2",
    "matilda": "XrExE9yKIg1WjnnlVkGX",
    "river": "SAz9YHcvj6GT2YYXdXww",
}
TEXT = "नमस्ते, मैं टैम इंफोसॉफ्ट से बोल रही हूँ। आपकी ईएमआई चार हज़ार पाँच सौ रुपये है, जो अट्ठाईस अक्टूबर को देय है।"

for name, vid in VOICES.items():
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{vid}",
        headers={"xi-api-key": os.environ["ELEVEN_API_KEY"]},
        json={"text": TEXT, "model_id": os.environ["TTS_MODEL"]},
    )
    if r.status_code == 200:
        open(f"tts_{name}.mp3", "wb").write(r.content)
        print(name, "saved")
    else:
        print(name, r.status_code, r.text[:150])