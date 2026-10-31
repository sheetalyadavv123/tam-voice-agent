import asyncio
import os
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from tools.followups import run_due_followups
from tools.pg_store import PostgresStore
from tools.rules import IST
from tools.store import MemoryStore, load_company_data

NOW = datetime(2026, 10, 28, 11, 0, tzinfo=IST)
ROOT = Path(__file__).parent.parent
PROMISE = {"id": 41, "customer_id": "C011", "promised_date": date(2026, 10, 27), "amount": 5400,
           "status": "open", "session_id": None, "created_at": NOW}
LINK = {"id": "PLold0001", "customer_id": "C002", "amount": 2200,
        "url": "https://pay.example.test/l/PLold0001", "channel": "sms", "status": "sent",
        "session_id": "old", "created_at": NOW, "paid_at": None}
CALLBACK = {"id": 7, "customer_id": "C001", "due_at": NOW, "reason": "customer_requested",
            "status": "open", "session_id": None, "created_at": NOW}
ATTEMPT = {"customer_id": "C001", "at": NOW, "session_id": "old", "kind": "reminder", "outcome": None}


def make(**rows):
    customers, seed = load_company_data()
    return PostgresStore(customers, seed, clock=lambda: NOW, rows=rows)


def test_seed_promises_are_not_loaded_from_files():
    assert make().promises_for("C011") == []


def test_loaded_rows_are_visible():
    s = make(promises=[PROMISE], links=[LINK], callbacks=[CALLBACK], attempts=[ATTEMPT])
    assert s.promises_for("C011", "open")[0]["id"] == 41
    assert s.latest_link("C002")["id"] == "PLold0001"
    assert len(s.callbacks_due(NOW)) == 1
    assert len(s.attempt_times("C001")) == 1


def test_new_ids_do_not_collide_with_loaded_ones():
    s = make(promises=[PROMISE], callbacks=[CALLBACK])
    assert s.add_promise("C002", date(2026, 10, 30), 2200)["id"] > 41
    assert s.add_callback("C002", NOW, "customer_requested")["id"] > 7


def test_status_change_becomes_an_update():
    s = make(promises=[PROMISE])
    s.set_promise_status(41, "kept")
    ch = s.changes()
    assert ch["promise_updates"] == [(41, "kept")]
    assert ch["new_promises"] == []


def test_new_rows_become_inserts():
    s = make()
    s.add_promise("C002", date(2026, 10, 30), 2200, "s1")
    s.add_payment_link("C002", 2200, "sms", "s1")
    s.add_callback("C002", NOW, "customer_requested", "s1")
    s.add_attempt("C002", NOW, "s1")
    ch = s.changes()
    assert [len(ch[k]) for k in ("new_promises", "new_links", "new_callbacks", "new_attempts")] == [1, 1, 1, 1]
    assert ch["promise_updates"] == [] and ch["link_updates"] == []


def test_loaded_attempts_are_not_inserted_again():
    s = make(attempts=[ATTEMPT])
    assert s.changes()["new_attempts"] == []
    s.add_attempt("C001", NOW, "s1")
    assert len(s.changes()["new_attempts"]) == 1


def test_paying_a_loaded_link_becomes_an_update():
    s = make(links=[LINK])
    s.mark_link_paid("PLold0001")
    ch = s.changes()
    assert [(l["id"], l["status"]) for l in ch["link_updates"]] == [("PLold0001", "paid")]
    assert ch["new_links"] == []


def test_new_promise_cancelled_in_the_same_call_is_inserted_with_its_final_status():
    s = make()
    p = s.add_promise("C002", date(2026, 10, 30), 2200)
    s.set_promise_status(p["id"], "cancelled")
    ch = s.changes()
    assert [x["status"] for x in ch["new_promises"]] == ["cancelled"]
    assert ch["promise_updates"] == []


def test_duplicate_open_promise_is_not_added_twice():
    s = make(promises=[PROMISE])
    again = s.add_promise("C011", date(2026, 10, 27), 5400)
    assert again["duplicate"] is True and s.changes()["new_promises"] == []


def test_attempt_outcome_is_saved_with_the_attempt():
    s = make()
    s.add_attempt("C002", NOW, "s1")
    s.set_attempt_outcome("s1", "paid_now")
    assert s.changes()["new_attempts"][0]["outcome"] == "paid_now"


def test_payment_link_ids_are_unique_across_stores():
    a, b = make(), make()
    assert a.add_payment_link("C002", 1, "sms", "x")["id"] != b.add_payment_link("C002", 1, "sms", "x")["id"]


def test_followups_can_skip_recording_attempts():
    s = MemoryStore.from_files(clock=lambda: NOW)
    acts = run_due_followups(s, datetime(2026, 10, 27, 10, tzinfo=IST), record_attempts=False)
    assert any(a["action"] == "call" and a["customer_id"] == "C011" for a in acts)
    assert s.attempt_times("C011") == []


# ---- optional: talks to your real Neon database, inside a transaction that is rolled back ----
DSN = dotenv_values(ROOT / ".env").get("DATABASE_URL") if os.getenv("RUN_DB_TESTS") == "1" else None
SESSION = "00000000-0000-0000-0000-000000000001"


@pytest.mark.skipif(not DSN, reason="set RUN_DB_TESTS=1 to run the Neon round-trip test")
def test_round_trip_against_neon_is_rolled_back():
    async def go():
        conn = await asyncpg.connect(DSN)
        tr = conn.transaction()
        await tr.start()
        try:
            s = await PostgresStore.load(DSN, ["C002"], clock=lambda: NOW, conn=conn)
            s.add_promise("C002", date(2026, 10, 30), 2200, SESSION)
            s.add_payment_link("C002", 2200, "sms", SESSION)
            s.add_callback("C002", NOW, "customer_requested", None)
            s.add_attempt("C002", NOW, SESSION, "reminder")
            s.set_attempt_outcome(SESSION, "promise_to_pay")
            counts = await s.flush(conn=conn)
            again = await PostgresStore.load(DSN, ["C002"], clock=lambda: NOW, conn=conn)
            return counts, again
        finally:
            await tr.rollback()
            await conn.close()

    counts, again = asyncio.run(go())
    assert counts["new_promises"] == 1 and counts["new_links"] == 1
    assert len(again.promises_for("C002", "open")) == 1
    assert again.latest_link("C002")["status"] == "sent"
    assert len(again.attempt_times("C002")) == 1

async def _two_calls_promise(conn, first, second, flush_order):
    """Two calls load C002 before either saves; each adds a promise; both flush."""
    # start clean inside the rolled-back transaction
    await conn.execute("UPDATE promises SET status = 'cancelled' WHERE customer_id = 'C002' AND status = 'open'")
    older = await PostgresStore.load(DSN, ["C002"], clock=lambda: NOW, conn=conn)
    newer = await PostgresStore.load(DSN, ["C002"], clock=lambda: NOW + timedelta(minutes=1), conn=conn)
    older.add_promise("C002", first, 2200, SESSION)
    newer.add_promise("C002", second, 2200, SESSION)
    stores = {"older": older, "newer": newer}
    for name in flush_order:
        await stores[name].flush(conn=conn)
    return await conn.fetch(
        "SELECT promised_date, status FROM promises WHERE customer_id = 'C002' AND session_id = $1 "
        "ORDER BY created_at", uuid.UUID(SESSION))


@pytest.mark.skipif(not DSN, reason="set RUN_DB_TESTS=1 to run the Neon round-trip test")
@pytest.mark.parametrize("flush_order", [("older", "newer"), ("newer", "older")])
def test_two_calls_leave_one_open_promise_the_newer_one(flush_order):
    async def go():
        conn = await asyncpg.connect(DSN)
        tr = conn.transaction()
        await tr.start()
        try:
            rows = await _two_calls_promise(conn, date(2026, 10, 30), date(2026, 10, 31), flush_order)
            open_now = await conn.fetch(
                "SELECT promised_date FROM promises WHERE customer_id = 'C002' AND status = 'open'")
            return rows, open_now
        finally:
            await tr.rollback()
            await conn.close()

    rows, open_now = asyncio.run(go())
    assert [r["promised_date"] for r in open_now] == [date(2026, 10, 31)]
    assert [(r["promised_date"], r["status"]) for r in rows] == [
        (date(2026, 10, 30), "cancelled"), (date(2026, 10, 31), "open")]


@pytest.mark.skipif(not DSN, reason="set RUN_DB_TESTS=1 to run the Neon round-trip test")
def test_two_calls_with_the_same_date_save_one_row():
    async def go():
        conn = await asyncpg.connect(DSN)
        tr = conn.transaction()
        await tr.start()
        try:
            return await _two_calls_promise(conn, date(2026, 10, 30), date(2026, 10, 30), ("older", "newer"))
        finally:
            await tr.rollback()
            await conn.close()

    rows = asyncio.run(go())
    assert [(r["promised_date"], r["status"]) for r in rows] == [(date(2026, 10, 30), "open")]


@pytest.mark.skipif(not DSN, reason="set RUN_DB_TESTS=1 to run the Neon round-trip test")
def test_promise_by_a_relative_saves_its_note_and_is_rolled_back():
    from tools.collection_tools import Call

    async def go():
        conn = await asyncpg.connect(DSN)
        tr = conn.transaction()
        await tr.start()
        try:
            s = await PostgresStore.load(DSN, ["C002"], clock=lambda: NOW, conn=conn)
            call = Call(s, "C002", session_id=SESSION, clock=lambda: NOW)
            call.heard("nahi, main unka bhai Rahul bol raha hoon")
            call.report_person("third_party")
            call.third_party = {"relationship": "brother", "name": "Rahul", "shared": True}
            assert call.record_promise_to_pay(30, 10, 2026)["saved"] is True
            await s.flush(conn=conn)
            return await conn.fetch(
                "SELECT promised_date, status, note FROM promises WHERE session_id = $1",
                uuid.UUID(SESSION))
        finally:
            await tr.rollback()
            await conn.close()

    rows = asyncio.run(go())
    assert [(r["promised_date"], r["status"], r["note"]) for r in rows] == [
        (date(2026, 10, 30), "open", "promised by brother Rahul")]
