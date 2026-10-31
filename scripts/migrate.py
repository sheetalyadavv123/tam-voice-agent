"""Run one SQL migration file against the database an env file points to.

Usage: python scripts/migrate.py <migration.sql> [env file]   (default env file: .env)
Prints only the host, never the password or the full connection string.
"""
import asyncio
import sys
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
from dotenv import dotenv_values


async def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    sql_file = Path(sys.argv[1])
    env_file = sys.argv[2] if len(sys.argv) > 2 else ".env"
    url = dotenv_values(env_file).get("DATABASE_URL")
    if not url:
        print(f"error: no DATABASE_URL in {env_file}")
        return 1
    print(f"migration: {sql_file}\nenv file: {env_file}\nhost: {urlparse(url).hostname}")
    conn = await asyncio.wait_for(asyncpg.connect(url), timeout=15)
    try:
        await conn.execute(sql_file.read_text(encoding="utf-8"))
        open_per_customer = await conn.fetch(
            "SELECT customer_id, count(*) AS n FROM promises WHERE status = 'open' "
            "GROUP BY customer_id ORDER BY customer_id")
    finally:
        await conn.close()
    print("done. open promises per customer:",
          ", ".join(f"{r['customer_id']}={r['n']}" for r in open_per_customer) or "none")
    return 0


sys.exit(asyncio.run(main()))
