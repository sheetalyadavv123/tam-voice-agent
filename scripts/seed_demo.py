import asyncio
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent"))

import asyncpg

import config
from tools.store import load_company_data


async def main():
    _, seed = load_company_data()
    conn = await asyncpg.connect(config.DATABASE_URL)
    added = 0
    for p in seed.get("open_promises", []):
        result = await conn.execute(
            "INSERT INTO promises (customer_id, promised_date, amount, status) VALUES ($1, $2, $3, 'open') "
            "ON CONFLICT (customer_id, promised_date) WHERE status = 'open' DO NOTHING",
            p["customer_id"], date.fromisoformat(p["promised_date"]), p["amount"])
        added += result.endswith(" 1")
    await conn.close()
    print(f"seeded {added} promises")


asyncio.run(main())