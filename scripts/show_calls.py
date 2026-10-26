import asyncio, sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).parent.parent / "agent"))
import config


async def main():
    conn = await asyncpg.connect(config.DATABASE_URL)
    calls = await conn.fetch(
        "SELECT id, started_at, duration_seconds, turn_count FROM call_sessions "
        "ORDER BY started_at DESC LIMIT 3")
    for c in calls:
        print(f"\n{c['id']}  {c['started_at']:%Y-%m-%d %H:%M}  "
              f"{c['duration_seconds']}s  {c['turn_count']} turns")
        for t in await conn.fetch(
                "SELECT role, text, interrupted FROM call_turns "
                "WHERE session_id=$1 ORDER BY turn_index", c["id"]):
            print(f"  {t['role']:9} {'(cut off) ' if t['interrupted'] else ''}{t['text']}")
    await conn.close()


asyncio.run(main())