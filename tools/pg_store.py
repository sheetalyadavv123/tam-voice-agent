"""A MemoryStore that is loaded from Neon before a call and saved back afterwards.

Company data (customers, payment records) still comes from the mock files. Only the
agent's own tables live in Postgres: call_attempts, promises, payment_links, callbacks.
Call flush() once, at the end of a call.

A customer has at most one open promise (a unique index makes sure). Two calls can
load the same customer before either saves, so flush() checks the database again
before it adds an open promise: the newest promise stays open, the older is cancelled.
"""
import logging
import uuid
from datetime import timedelta

import asyncpg

from tools.rules import now_ist
from tools.store import MemoryStore, load_company_data

logger = logging.getLogger("tam-voice-agent")
ATTEMPT_LOOKBACK_DAYS = 8
_FILTER = "($1::text[] IS NULL OR customer_id = ANY($1::text[]))"


def _uuid_or_none(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def _clean(record):
    row = dict(record)
    if row.get("session_id") is not None:
        row["session_id"] = str(row["session_id"])
    return row


class PostgresStore(MemoryStore):
    def __init__(self, customers, seed, clock=now_ist, rows=None, dsn=None):
        # The seed's open promises live in the database, so they are not loaded from files.
        super().__init__(customers, {**seed, "open_promises": []}, clock)
        rows = rows or {}
        self._dsn = dsn
        self._flushed = False
        self._promises = [dict(r) for r in rows.get("promises", [])]
        self._callbacks = [dict(r) for r in rows.get("callbacks", [])]
        self._links = [dict(r) for r in rows.get("links", [])]
        self._attempts = [{**dict(r), "loaded": True} for r in rows.get("attempts", [])]
        # New ids start above every loaded id, so they can never be mistaken for database rows.
        self._seq = {"promise": max([p["id"] for p in self._promises], default=0),
                     "callback": max([c["id"] for c in self._callbacks], default=0)}
        self._base = {
            "promises": {p["id"]: p["status"] for p in self._promises},
            "callbacks": {c["id"]: c["status"] for c in self._callbacks},
            "links": {l["id"]: l["status"] for l in self._links},
        }

    @classmethod
    async def load(cls, dsn, customer_ids=None, clock=now_ist, conn=None):
        """customer_ids=None loads everyone (for the scheduler)."""
        customers, seed = load_company_data()
        ids = list(customer_ids) if customer_ids is not None else None
        since = clock() - timedelta(days=ATTEMPT_LOOKBACK_DAYS)
        own = conn is None
        if own:
            conn = await asyncpg.connect(dsn)
        try:
            promises = await conn.fetch(
                "SELECT id, customer_id, promised_date, amount, status, session_id, created_at "
                f"FROM promises WHERE status = 'open' AND {_FILTER}", ids)
            callbacks = await conn.fetch(
                "SELECT id, customer_id, due_at, reason, status, session_id, created_at "
                f"FROM callbacks WHERE status = 'open' AND {_FILTER}", ids)
            links = await conn.fetch(
                "SELECT DISTINCT ON (customer_id) id, customer_id, amount, url, channel, status, "
                "session_id, created_at, paid_at FROM payment_links "
                f"WHERE {_FILTER} ORDER BY customer_id, created_at DESC", ids)
            attempts = await conn.fetch(
                "SELECT customer_id, attempted_at AS at, session_id, kind, outcome "
                f"FROM call_attempts WHERE attempted_at >= $2 AND {_FILTER}", ids, since)
        finally:
            if own:
                await conn.close()
        rows = {"promises": [_clean(r) for r in promises],
                "callbacks": [_clean(r) for r in callbacks],
                "links": [_clean(r) for r in links],
                "attempts": [_clean(r) for r in attempts]}
        return cls(customers, seed, clock=clock, rows=rows, dsn=dsn)

    def changes(self):
        """Everything that is new or different since loading."""
        base = self._base
        return {
            "new_attempts": [dict(a) for a in self._attempts if not a.get("loaded")],
            "new_promises": [dict(p) for p in self._promises if p["id"] not in base["promises"]],
            "promise_updates": [(p["id"], p["status"]) for p in self._promises
                                if p["id"] in base["promises"] and p["status"] != base["promises"][p["id"]]],
            "new_callbacks": [dict(c) for c in self._callbacks if c["id"] not in base["callbacks"]],
            "callback_updates": [(c["id"], c["status"]) for c in self._callbacks
                                 if c["id"] in base["callbacks"] and c["status"] != base["callbacks"][c["id"]]],
            "new_links": [dict(l) for l in self._links if l["id"] not in base["links"]],
            "link_updates": [dict(l) for l in self._links
                             if l["id"] in base["links"] and l["status"] != base["links"][l["id"]]],
        }

    async def flush(self, conn=None):
        """Save the changes in one transaction. Call it once per store."""
        if self._flushed:
            raise RuntimeError("flush() can only be called once per store")
        if conn is None and not self._dsn:
            raise ValueError("no connection: pass conn= or load the store with a dsn")
        ch = self.changes()
        own = conn is None
        if own:
            conn = await asyncpg.connect(self._dsn)
        try:
            async with conn.transaction():
                for pid, status in ch["promise_updates"]:
                    await conn.execute("UPDATE promises SET status = $1 WHERE id = $2", status, pid)
                for cid, status in ch["callback_updates"]:
                    await conn.execute("UPDATE callbacks SET status = $1 WHERE id = $2", status, cid)
                for l in ch["link_updates"]:
                    await conn.execute("UPDATE payment_links SET status = $1, paid_at = $2 WHERE id = $3",
                                       l["status"], l["paid_at"], l["id"])
                for p in ch["new_promises"]:
                    status = p["status"]
                    if status == "open":
                        status = await self._open_promise_status(conn, p)
                        if status is None:
                            continue  # the same date is already open: a duplicate, not a new row
                    await conn.execute(
                        "INSERT INTO promises (customer_id, promised_date, amount, status, session_id, created_at) "
                        "VALUES ($1, $2, $3, $4, $5, $6) "
                        "ON CONFLICT (customer_id) WHERE status = 'open' DO NOTHING",
                        p["customer_id"], p["promised_date"], p["amount"], status,
                        _uuid_or_none(p["session_id"]), p["created_at"])
                for c in ch["new_callbacks"]:
                    await conn.execute(
                        "INSERT INTO callbacks (customer_id, due_at, reason, status, session_id, created_at) "
                        "VALUES ($1, $2, $3, $4, $5, $6)",
                        c["customer_id"], c["due_at"], c["reason"], c["status"],
                        _uuid_or_none(c["session_id"]), c["created_at"])
                for l in ch["new_links"]:
                    await conn.execute(
                        "INSERT INTO payment_links (id, customer_id, amount, url, channel, status, "
                        "session_id, created_at, paid_at) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) "
                        "ON CONFLICT (id) DO NOTHING",
                        l["id"], l["customer_id"], l["amount"], l["url"], l["channel"], l["status"],
                        _uuid_or_none(l["session_id"]), l["created_at"], l["paid_at"])
                for a in ch["new_attempts"]:
                    await conn.execute(
                        "INSERT INTO call_attempts (customer_id, attempted_at, session_id, kind, outcome) "
                        "VALUES ($1, $2, $3, $4, $5)",
                        a["customer_id"], a["at"], _uuid_or_none(a["session_id"]), a["kind"], a["outcome"])
        finally:
            if own:
                await conn.close()
        self._flushed = True
        return {k: len(v) for k, v in ch.items()}

    @staticmethod
    async def _open_promise_status(conn, p):
        """How to save a new open promise, given what other calls saved since we loaded:
        "open" (older open promises are cancelled first), "cancelled" (a newer promise is
        already open) or None (the same date is already open, so nothing new to save)."""
        cid = p["customer_id"]
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", cid)  # one flush per customer at a time
        rows = await conn.fetch(
            "SELECT promised_date, created_at FROM promises WHERE customer_id = $1 AND status = 'open'", cid)
        if any(r["promised_date"] == p["promised_date"] for r in rows):
            logger.info("RULE one_open_promise: %s %s already open, not saved twice", cid, p["promised_date"])
            return None
        if any(r["created_at"] > p["created_at"] for r in rows):
            logger.info("RULE one_open_promise: %s has a newer open promise, %s saved as cancelled",
                        cid, p["promised_date"])
            return "cancelled"
        done = await conn.execute(
            "UPDATE promises SET status = 'cancelled' WHERE customer_id = $1 AND status = 'open'", cid)
        if done != "UPDATE 0":
            logger.info("RULE one_open_promise: %s older open promise cancelled for %s", cid, p["promised_date"])
        return "open"