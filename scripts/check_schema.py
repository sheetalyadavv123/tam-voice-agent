"""Does a database have every table and column the code needs? The same check the agent
runs at startup. Run it on staging before deploying (README, Deploying).

Usage: python scripts/check_schema.py <env file>
Reads only DATABASE_URL from the env file, prints only its host, and changes nothing.
Exit code 0 when the schema matches, 1 when columns are missing or it can't connect.
"""
import asyncio
import sys
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).parent.parent))
from tools import schema_check  # noqa: E402


async def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 1
    env_file = Path(argv[1])
    if not env_file.is_file():
        print(f"error: {env_file} not found")
        return 1
    url = dotenv_values(env_file).get("DATABASE_URL")
    if not url:
        print(f"error: no DATABASE_URL in {env_file}")
        return 1
    print(f"env file: {env_file}\nhost: {urlparse(url).hostname}")
    try:
        conn = await asyncio.wait_for(asyncpg.connect(url), timeout=15)
    except Exception as e:  # the message can include the URL, so only the type
        print("connect failed:", type(e).__name__)
        return 1
    try:
        result = await schema_check.check(conn)
    finally:
        await conn.close()
    print(schema_check.message(result))
    return 1 if result["missing"] else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv)))
