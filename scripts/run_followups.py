import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent"))
sys.stdout.reconfigure(encoding="utf-8")

import config
from tools.followups import run_due_followups
from tools.pg_store import PostgresStore
from tools.rules import now_ist


def number_checks(actions):
    """Customers whose last call reached a wrong number, once each, in order."""
    return list(dict.fromkeys(a["customer_id"] for a in actions
                              if a.get("reason") == "needs_number_check"))


async def main():
    apply = "--apply" in sys.argv
    store = await PostgresStore.load(config.DATABASE_URL)
    actions = run_due_followups(store, now_ist(), record_attempts=False)
    checks = number_checks(actions)
    actions = [a for a in actions if a.get("reason") != "needs_number_check"]
    if not actions and not checks:
        print("nothing is due right now")
    for a in actions:
        detail = a.get("kind") or a.get("reason") or ""
        print(f"{a['action']:<18} {a['customer_id']:<6} {detail} {a.get('when', '')}")
    if checks:
        print("\nneeds number check (last call reached a wrong number; not called until a "
              "person fixes the number):")
        for cid in checks:
            print(f"  {cid:<6} {store.get_customer(cid)['name']}")
    if apply:
        print("saved:", {k: v for k, v in (await store.flush()).items() if v})
    else:
        print("\nDry run: nothing was saved. Add --apply to save status changes.")


if __name__ == "__main__":
    asyncio.run(main())
