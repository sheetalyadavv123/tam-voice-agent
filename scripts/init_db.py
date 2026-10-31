import asyncio, sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).parent.parent / "agent"))
import config


async def main():
    sql = (Path(__file__).parent.parent / "db" / "schema.sql").read_text(encoding="utf-8")
    conn = await asyncpg.connect(config.DATABASE_URL)
    try:
        await conn.execute(sql)
    except asyncpg.UniqueViolationError:
        # An older database with two open promises for one customer: the schema's
        # one-open-promise index can't be built until the migration tidies them up.
        sys.exit("Schema not applied: a customer has more than one open promise. Run\n"
                 "  python scripts/migrate.py db/migrations/001_one_open_promise.sql\nfirst.")
    finally:
        await conn.close()
    print("Schema applied")


asyncio.run(main())
