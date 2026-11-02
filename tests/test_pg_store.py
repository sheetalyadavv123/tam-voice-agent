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


async def _begin(conn):
    """Start the test's transaction and apply the newer migrations inside it, so a
    database that hasn't been migrated yet is not changed (it is all rolled back)."""
    tr = conn.transaction()
    await tr.start()
    for name in ("003_number_check.sql", "004_callback_note.sql"):
        sql = (ROOT / "db" / "migrations" / name).read_text(encoding="utf-8")
        await conn.execute(sql.replace("BEGIN;", "").replace("COMMIT;", ""))
    return tr


@pytest.mark.skipif(not DSN, reason="set RUN_DB_TESTS=1 to run the Neon round-trip test")
def test_round_trip_against_neon_is_rolled_back():
    async def go():
        conn = await asyncpg.connect(DSN)
        tr = await _begin(conn)
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
        tr = await _begin(conn)
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
        tr = await _begin(conn)
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
        tr = await _begin(conn)
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


# ---- batch 5: the rows the two new rules write, through the real agent hook ----

async def _agent_call(conn, lines):
    """One call for C002 through the agent: the attempt is added as entrypoint() does
    with TEST_MODE off, the caller's lines are heard, then the call is saved as
    finish() saves it. Returns (rows, call)."""
    import json
    import sys
    sys.path.insert(0, str(ROOT / "agent"))
    import db
    import main
    from livekit.agents import StopResponse, llm
    from tools.collection_tools import Call

    class Session:
        tts = None  # a language switch checks it

        def say(self, text):
            class H:
                async def wait_for_playout(self):
                    pass
            return H()

        def shutdown(self):
            pass

        def _conversation_item_added(self, item):
            pass

    sid = uuid.UUID(SESSION)
    store = await PostgresStore.load(DSN, ["C002"], clock=lambda: NOW, conn=conn)
    store.add_attempt("C002", NOW, SESSION, "reminder", phone=store.get_customer("C002")["phone"])
    call = Call(store, "C002", session_id=SESSION, clock=lambda: NOW)
    agent = main.Collector(call, "x")
    old = main.Collector.session
    main.Collector.session = property(lambda self: Session())
    try:
        for text in lines:
            try:
                await agent.on_user_turn_completed(None, llm.ChatMessage(role="user", content=[text]))
            except StopResponse:
                if agent._end_task:
                    await agent._end_task
    finally:
        main.Collector.session = old
    call.finalize_outcome()
    store.set_attempt_outcome(SESSION, call.outcome)
    await store.flush(conn=conn)
    turns = [{"role": "user", "text": t, "interrupted": False, "created_at": NOW} for t in lines]
    await db.save_call(sid, "test-room", "agent", NOW, NOW, turns, outcome=call.outcome,
                       metadata=call.summary(), conn=conn)
    q = lambda sql: conn.fetch(sql, sid)
    rows = {
        "session": await q("SELECT outcome, metadata FROM call_sessions WHERE id = $1"),
        "attempts": await q("SELECT customer_id, kind, outcome FROM call_attempts WHERE session_id = $1"),
        "callbacks": await q("SELECT customer_id, due_at, reason, status FROM callbacks WHERE session_id = $1"),
        "callback_notes": await q("SELECT note FROM callbacks WHERE session_id = $1"),
        "promises": await q("SELECT id FROM promises WHERE session_id = $1"),
        "links": await q("SELECT id FROM payment_links WHERE session_id = $1"),
    }
    rows["meta"] = json.loads(rows["session"][0]["metadata"])
    return rows, call


def _rolled_back(lines):
    async def go():
        conn = await asyncpg.connect(DSN)
        tr = await _begin(conn)
        try:
            return await _agent_call(conn, lines)
        finally:
            await tr.rollback()
            await conn.close()
    return asyncio.run(go())


@pytest.mark.skipif(not DSN, reason="set RUN_DB_TESTS=1 to run the Neon round-trip test")
def test_relative_who_cant_commit_saves_one_callback_and_no_promise():
    rows, call = _rolled_back(["nahi, main unka bhai Rahul bol raha hoon", "haan bataiye",
                               "2 November 1985", "pata nahi, unse hi poochna padega",
                               "kal shaam 6 baje"])
    assert [tuple(r) for r in rows["callbacks"]] == [
        ("C002", datetime(2026, 10, 29, 18, 0, tzinfo=IST), "customer_unavailable", "open")]
    assert rows["callbacks"][0]["due_at"].astimezone(IST).hour == 18  # 6 pm IST
    assert rows["promises"] == [] and rows["links"] == []
    assert [tuple(r) for r in rows["attempts"]] == [("C002", "reminder", "third_party")]
    assert rows["session"][0]["outcome"] == "third_party"
    assert rows["meta"]["note"] == "relative could not commit, callback booked"
    assert rows["meta"]["third_party"]["relationship"] == "brother"
    assert rows["meta"]["third_party"]["name"] == "Rahul"
    assert rows["meta"]["promise_recorded"] is False and rows["meta"]["callback_scheduled"] is True


@pytest.mark.skipif(not DSN, reason="set RUN_DB_TESTS=1 to run the Neon round-trip test")
def test_wrong_number_saves_only_the_outcome():
    rows, call = _rolled_back(["kaun Sunita? Main kisi Sunita ko nahi jaanta"])
    assert rows["session"][0]["outcome"] == "wrong_number"
    assert [tuple(r) for r in rows["attempts"]] == [("C002", "reminder", "wrong_number")]
    assert rows["callbacks"] == [] and rows["promises"] == [] and rows["links"] == []
    assert rows["meta"]["third_party"] is None and rows["meta"]["callback_scheduled"] is False


@pytest.mark.skipif(not DSN, reason="set RUN_DB_TESTS=1 to run the Neon round-trip test")
def test_scheduler_sees_an_old_wrong_number_attempt():
    async def go():
        conn = await asyncpg.connect(DSN)
        tr = await _begin(conn)
        try:
            await conn.execute(
                "INSERT INTO call_attempts (customer_id, attempted_at, session_id, kind, outcome) "
                "VALUES ('C002', $1, $2, 'reminder', 'wrong_number')",
                NOW - timedelta(days=30), uuid.UUID(SESSION))
            await conn.execute("DELETE FROM call_attempts WHERE customer_id = 'C002' "
                               "AND attempted_at > $1", NOW - timedelta(days=30))
            s = await PostgresStore.load(DSN, ["C002"], clock=lambda: NOW, conn=conn)
            return s.number_check_hold("C002"), s.attempt_times("C002")
        finally:
            await tr.rollback()
            await conn.close()

    hold, times = asyncio.run(go())
    assert hold["outcome"] == "wrong_number" and len(times) == 1


async def _wrong_number_only(conn, phone):
    """C002's only finished attempt: a wrong-number call to `phone`."""
    from tools.store import phone_hash
    await conn.execute("DELETE FROM call_attempts WHERE customer_id = 'C002'")
    return await conn.fetchval(
        "INSERT INTO call_attempts (customer_id, attempted_at, session_id, kind, outcome, phone_hash) "
        "VALUES ('C002', $1, $2, 'reminder', 'wrong_number', $3) RETURNING id",
        NOW - timedelta(days=1), uuid.UUID(SESSION), phone_hash(phone))


def _c002_phone():
    customers, _ = load_company_data()
    return next(c["phone"] for c in customers if c["id"] == "C002")


@pytest.mark.skipif(not DSN, reason="set RUN_DB_TESTS=1 to run the Neon round-trip test")
def test_clearing_the_number_check_marks_one_row_and_lifts_the_hold():
    async def go():
        conn = await asyncpg.connect(DSN)
        tr = await _begin(conn)
        try:
            attempt_id = await _wrong_number_only(conn, _c002_phone())
            before = await PostgresStore.load(DSN, ["C002"], clock=lambda: NOW, conn=conn)
            first = await PostgresStore.clear_number_check(conn, "C002", NOW)
            second = await PostgresStore.clear_number_check(conn, "C002", NOW)
            after = await PostgresStore.load(DSN, ["C002"], clock=lambda: NOW, conn=conn)
            rows = await conn.fetch(
                "SELECT id, outcome, number_checked_at FROM call_attempts WHERE customer_id = 'C002'")
            return attempt_id, before, first, second, after, rows
        finally:
            await tr.rollback()
            await conn.close()

    attempt_id, before, first, second, after, rows = asyncio.run(go())
    assert before.number_check_hold("C002") is not None
    assert first["cleared"] is True and first["attempt"]["id"] == attempt_id
    assert second == {"cleared": False, "reason": "already_checked", "attempt": second["attempt"]}
    assert [(r["id"], r["outcome"], r["number_checked_at"]) for r in rows] == [(attempt_id, "wrong_number", NOW)]
    assert after.number_check_hold("C002") is None
    acts = run_due_followups(after, NOW, record_attempts=False)
    assert not any(a.get("reason") == "needs_number_check" for a in acts)


@pytest.mark.skipif(not DSN, reason="set RUN_DB_TESTS=1 to run the Neon round-trip test")
def test_a_changed_phone_number_lifts_the_hold_by_itself():
    async def go():
        conn = await asyncpg.connect(DSN)
        tr = await _begin(conn)
        try:
            await _wrong_number_only(conn, "+91 90000 00000")  # not C002's number now
            changed = await PostgresStore.load(DSN, ["C002"], clock=lambda: NOW, conn=conn)
            await _wrong_number_only(conn, _c002_phone())
            same = await PostgresStore.load(DSN, ["C002"], clock=lambda: NOW, conn=conn)
            return changed, same
        finally:
            await tr.rollback()
            await conn.close()

    changed, same = asyncio.run(go())
    assert changed.number_check_hold("C002") is None
    assert same.number_check_hold("C002") is not None


@pytest.mark.skipif(not DSN, reason="set RUN_DB_TESTS=1 to run the Neon round-trip test")
def test_the_call_saves_the_dialled_numbers_hash():
    from tools.store import phone_hash

    async def go():
        conn = await asyncpg.connect(DSN)
        tr = await _begin(conn)
        try:
            s = await PostgresStore.load(DSN, ["C002"], clock=lambda: NOW, conn=conn)
            s.add_attempt("C002", NOW, SESSION, "reminder", phone=_c002_phone())
            await s.flush(conn=conn)
            return await conn.fetchval("SELECT phone_hash FROM call_attempts WHERE session_id = $1",
                                       uuid.UUID(SESSION))
        finally:
            await tr.rollback()
            await conn.close()

    saved = asyncio.run(go())
    assert saved == phone_hash(_c002_phone()) and _c002_phone() not in saved


# ---- batch 6: day-only callbacks and the schema check, against Neon ----

THURSDAY_11 = datetime(2026, 10, 29, 11, 0, tzinfo=IST)  # "kal" from NOW


@pytest.mark.skipif(not DSN, reason="set RUN_DB_TESTS=1 to run the Neon round-trip test")
def test_sister_cant_commit_day_only_saves_the_callback_at_the_default_hour():
    rows, call = _rolled_back(["Sunita ghar pe nahi hai", "main unki behen hoon", "haan bataiye",
                               "2 November 1985", "pata nahi, unse hi poochna padega", "kal",
                               "pata nahi kab"])
    assert [tuple(r) for r in rows["callbacks"]] == [
        ("C002", THURSDAY_11, "customer_unavailable", "open")]
    assert rows["callbacks"][0]["due_at"].astimezone(IST).hour == 11
    assert [r["note"] for r in rows["callback_notes"]] == ["caller gave day only"]
    assert rows["promises"] == [] and rows["links"] == []
    assert rows["session"][0]["outcome"] == "third_party"
    assert rows["meta"]["note"] == "relative could not commit, callback booked; caller gave day only"
    assert rows["meta"]["third_party"]["relationship"] == "sister"


@pytest.mark.skipif(not DSN, reason="set RUN_DB_TESTS=1 to run the Neon round-trip test")
def test_busy_customer_day_only_saves_a_customer_requested_callback():
    rows, call = _rolled_back(["haan, abhi busy hoon", "Monday call karna", "pata nahi"])
    assert [tuple(r) for r in rows["callbacks"]] == [
        ("C002", datetime(2026, 11, 2, 11, 0, tzinfo=IST), "customer_requested", "open")]
    assert [r["note"] for r in rows["callback_notes"]] == ["caller gave day only"]
    assert rows["session"][0]["outcome"] == "callback_requested"
    assert [tuple(r) for r in rows["attempts"]] == [("C002", "reminder", "callback_requested")]
    assert rows["meta"]["person_status"] == "unavailable" and rows["promises"] == []


@pytest.mark.skipif(not DSN, reason="set RUN_DB_TESTS=1 to run the Neon round-trip test")
def test_busy_before_identity_console_transcript_saves_a_customer_unavailable_callback():
    rows, call = _rolled_back([
        "Actually, I'm quite occupied. Right? Now. So can you please call me on Monday?",
        "You can call me whenever"])
    assert [tuple(r) for r in rows["callbacks"]] == [
        ("C002", datetime(2026, 11, 2, 11, 0, tzinfo=IST), "customer_unavailable", "open")]
    assert [r["note"] for r in rows["callback_notes"]] == ["caller said any time"]
    assert rows["session"][0]["outcome"] == "not_available"
    assert rows["meta"]["note"] == "identity not confirmed, callback booked; caller said any time"
    assert rows["meta"]["person_status"] == "unavailable"
    assert rows["promises"] == [] and rows["links"] == []


@pytest.mark.skipif(not DSN, reason="set RUN_DB_TESTS=1 to run the Neon round-trip test")
def test_busy_yes_monday_any_time_saves_at_once():
    rows, call = _rolled_back(["haan, abhi busy hoon, Monday kisi bhi time"])
    assert [tuple(r) for r in rows["callbacks"]] == [
        ("C002", datetime(2026, 11, 2, 11, 0, tzinfo=IST), "customer_requested", "open")]
    assert [r["note"] for r in rows["callback_notes"]] == ["caller said any time"]
    assert rows["session"][0]["outcome"] == "callback_requested"


@pytest.mark.skipif(not DSN, reason="set RUN_DB_TESTS=1 to run the Neon round-trip test")
def test_a_callback_with_a_time_saves_no_note():
    rows, call = _rolled_back(["haan, abhi busy hoon", "kal subah 10 baje"])
    assert [r["note"] for r in rows["callback_notes"]] == [None]


@pytest.mark.skipif(not DSN, reason="set RUN_DB_TESTS=1 to run the Neon round-trip test")
def test_schema_check_against_neon_finds_a_dropped_column_and_is_rolled_back():
    from tools import schema_check

    async def go():
        conn = await asyncpg.connect(DSN)
        tr = await _begin(conn)  # 003 and 004 applied inside the transaction
        try:
            migrated = await schema_check.check(conn)
            await conn.execute("ALTER TABLE callbacks DROP COLUMN note")
            dropped = await schema_check.check(conn)
            return migrated, dropped
        finally:
            await tr.rollback()
            await conn.close()

    migrated, dropped = asyncio.run(go())
    assert migrated == {"missing": [], "migrations": []}
    assert dropped == {"missing": ["callbacks.note"],
                       "migrations": ["db/migrations/004_callback_note.sql"]}
