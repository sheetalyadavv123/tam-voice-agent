import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "agent"))

import asyncpg

import config


async def main():
    answer = input("This deletes ALL promises, links, callbacks and attempts in the dev "
                   "database. Type RESET to continue: ")
    if answer.strip() != "RESET":
        print("cancelled")
        return
    conn = await asyncpg.connect(config.DATABASE_URL)
    await conn.execute("TRUNCATE call_attempts, promises, payment_links, callbacks RESTART IDENTITY")
    await conn.close()
    print("cleared. Run scripts/seed_demo.py to add the example promises again")


asyncio.run(main())