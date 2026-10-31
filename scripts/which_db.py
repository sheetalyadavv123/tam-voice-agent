"""Show which database an env file points to, and its newest calls.

Usage: python scripts/which_db.py [env file]   (default: .env)
Prints only the host, never the password or the full connection string.
"""
import asyncio
import sys
from urllib.parse import urlparse

import asyncpg
from dotenv import dotenv_values


async def main():
    env_file = sys.argv[1] if len(sys.argv) > 1 else ".env"
    values = dotenv_values(env_file)
    print("file:", env_file)
    if not values:
        print("error: file not found or empty")
        return 1
    url = values.get("DATABASE_URL")
    if not url:
        print("error: no DATABASE_URL in this file")
        return 1
    parsed = urlparse(url)
    print("host:", parsed.hostname)
    print("database:", parsed.path.lstrip("/"), "user:", parsed.username)

    try:
        conn = await asyncio.wait_for(asyncpg.connect(url), timeout=15)
    except Exception as e:
        # The message of some errors can include the URL, so show only the type
        print("connect failed:", type(e).__name__)
        return 1
    try:
        rows = await conn.fetch(
            "SELECT started_at, room_name, outcome FROM call_sessions "
            "ORDER BY started_at DESC LIMIT 5")
    except Exception as e:
        print("query failed:", type(e).__name__)
        return 1
    finally:
        await conn.close()

    print("newest call_sessions:" if rows else "call_sessions is empty")
    for r in rows:
        print(" ", r["started_at"], r["room_name"], r["outcome"])
    return 0


sys.exit(asyncio.run(main()))
