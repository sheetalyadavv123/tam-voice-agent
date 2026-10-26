import asyncio, sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).parent.parent / "agent"))
import config


async def main():
    sql = (Path(__file__).parent.parent / "db" / "schema.sql").read_text(encoding="utf-8")
    conn = await asyncpg.connect(config.DATABASE_URL)
    await conn.execute(sql)
    await conn.close()
    print("Schema applied")


asyncio.run(main())