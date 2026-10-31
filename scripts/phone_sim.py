"""Make sample clips of the agent's voice, clean and as they would sound on a phone call.

Usage: python scripts/phone_sim.py
Uses SARVAM_SPEAKER and TTS_PACE from agent/config.py.
Saves audio_test/clean/<voice>_pace<pace>_<line>.wav and the same names in audio_test/phone/.
"""
import base64
import os
import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from agent import config  # noqa: E402

OUT = ROOT / "audio_test"
LINES = {
    "greeting": "नमस्ते, मैं Tam Infosoft की AI असिस्टेंट बोल रही हूँ। "
                "क्या मेरी बात Sunita जी से हो रही है?",
    "amount": "Sunita जी, आपकी ₹2,200 की EMI 10 September को due थी। "
              "आप इसे कब pay कर पाएँगी?",
    "confirm": "ठीक है, ₹2,200, 30 October को। मैंने note कर लिया है।",
}


def find_ffmpeg():
    packages = Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "WinGet" / "Packages"
    found = sorted(packages.glob("*FFmpeg*/**/bin/ffmpeg.exe"))
    if not found:
        sys.exit(f"ffmpeg.exe not found under {packages}")
    return found[-1]


def speak(client, key, voice, pace, text):
    resp = client.post(
        "https://api.sarvam.ai/text-to-speech",
        headers={"api-subscription-key": key},
        json={"text": text, "target_language_code": "hi-IN", "speaker": voice,
              "model": "bulbul:v3", "pace": pace, "speech_sample_rate": 24000},
    )
    if resp.status_code != 200:
        raise ValueError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    return b"".join(base64.b64decode(a) for a in resp.json()["audios"])


def to_phone(ffmpeg, src, dst):
    """Phone band, 8 kHz mono, G.711 mu-law, then back to plain 16-bit wav."""
    mulaw = dst.with_suffix(".mulaw.wav")
    run = lambda *args: subprocess.run([str(ffmpeg), "-y", "-loglevel", "error", *args], check=True)
    run("-i", str(src), "-af", "highpass=f=300,lowpass=f=3400",
        "-ar", "8000", "-ac", "1", "-c:a", "pcm_mulaw", str(mulaw))
    run("-i", str(mulaw), "-c:a", "pcm_s16le", str(dst))
    mulaw.unlink()


def main():
    key = os.getenv("SARVAM_API_KEY")
    if not key:
        sys.exit("SARVAM_API_KEY is not set in .env")
    ffmpeg = find_ffmpeg()
    (OUT / "clean").mkdir(parents=True, exist_ok=True)
    (OUT / "phone").mkdir(parents=True, exist_ok=True)

    voice, pace = config.SARVAM_SPEAKER, config.TTS_PACE
    with httpx.Client(timeout=60) as client:
        for line, text in LINES.items():
            name = f"{voice}_pace{pace}_{line}.wav"
            try:
                audio = speak(client, key, voice, pace, text)
            except ValueError as e:
                sys.exit(f"Sarvam rejected {voice}: {e}")
            clean = OUT / "clean" / name
            clean.write_bytes(audio)
            to_phone(ffmpeg, clean, OUT / "phone" / name)
            print("saved", name)

    print(f"\n{len(LINES)} clips in {OUT / 'clean'} and {OUT / 'phone'}")


if __name__ == "__main__":
    main()
