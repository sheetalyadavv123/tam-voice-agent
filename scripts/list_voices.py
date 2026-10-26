import os, requests
from dotenv import load_dotenv

load_dotenv()
r = requests.get(
    "https://api.elevenlabs.io/v1/voices",
    headers={"xi-api-key": os.environ["ELEVEN_API_KEY"]},
)
print(r.status_code)
if r.status_code == 200:
    for v in r.json()["voices"]:
        print(v.get("category"), "|", v["name"], "|", v["voice_id"])
else:
    print(r.text[:300])