"""Clear the wrong-number hold on one customer, after a person has checked the number.

Usage: python scripts/clear_number_check.py <customer_id> <env file>
The env file is required: only the database in that file is touched. Prints only the
host, never the password or the full connection string. Changes at most one row
(call_attempts.number_checked_at on the customer's latest wrong-number attempt).
A changed phone number in the company records lifts the hold by itself; this is for
when the number was checked and is right.
"""
import asyncio
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import asyncpg  # noqa: E402
from dotenv import dotenv_values  # noqa: E402

from tools.pg_store import PostgresStore  # noqa: E402
from tools.rules import now_ist  # noqa: E402
from tools.store import load_company_data  # noqa: E402


def describe(customer_id, result):
    """What the run changed, in words."""
    a = result.get("attempt")
    when = f"{a['attempted_at']:%Y-%m-%d %H:%M %Z}" if a else ""
    if result["cleared"]:
        return (f"cleared: {customer_id} wrong-number hold lifted. call_attempts id {a['id']} "
                f"(call at {when}) number_checked_at set to {a['number_checked_at']:%Y-%m-%d %H:%M %Z}. "
                "The scheduler may call this customer again.")
    reasons = {
        "no_finished_attempts": "no finished calls on record",
        "latest_attempt_not_wrong_number": f"the latest finished call ({when}) was {a and a['outcome']}",
        "already_checked": f"the wrong-number call at {when} was already marked checked",
    }
    return f"nothing changed: {customer_id} has no wrong-number hold ({reasons[result['reason']]})."


async def run(customer_id, env_file):
    if not Path(env_file).is_file():
        print(f"error: env file not found: {env_file}")
        return 1
    url = dotenv_values(env_file).get("DATABASE_URL")
    if not url:
        print(f"error: no DATABASE_URL in {env_file}")
        return 1
    customers, _ = load_company_data()
    if customer_id not in {c["id"] for c in customers}:
        print(f"error: unknown customer {customer_id}")
        return 1
    print(f"env file: {env_file}\nhost: {urlparse(url).hostname}")
    conn = await asyncio.wait_for(asyncpg.connect(url), timeout=15)
    try:
        async with conn.transaction():
            result = await PostgresStore.clear_number_check(conn, customer_id, now_ist())
    finally:
        await conn.close()
    print(describe(customer_id, result))
    return 0


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    return asyncio.run(run(argv[1], argv[2]))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv))
