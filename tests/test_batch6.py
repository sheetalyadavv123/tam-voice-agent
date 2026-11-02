"""Batch 6: the startup schema check, day-only callbacks, and fuzzy names needing a yes."""
import asyncio
import logging
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

from tests.test_fixed_replies import (  # noqa: F401  (fake_session is an autouse fixture)
    FAR, NOW, fake_session, hear, make_agent, run_tool, says, verified_brother,
)
from tests.helpers import confirmed
from tools import caller_words, names, schema_check
from tools.callback_ask import TimeAsk
from tools.call_later import asks_call_later
from tools.rules import IST

import main  # noqa: E402  (agent/ is on the path via test_fixed_replies)

ROOT = Path(__file__).parent.parent
THURSDAY_11 = datetime(2026, 10, 29, 11, 0, tzinfo=IST)  # "kal" from Wednesday 28 October
MONDAY_11 = datetime(2026, 11, 2, 11, 0, tzinfo=IST)


# ======================= 1. the startup schema check =======================

def test_the_schema_file_lists_every_column_the_code_uses():
    need = schema_check.needed()
    for col in [("call_attempts", "phone_hash"), ("call_attempts", "number_checked_at"),
                ("promises", "note"), ("callbacks", "note"), ("call_turns", "interrupted"),
                ("call_sessions", "metadata"), ("payment_links", "paid_at")]:
        assert col in need
    assert not any(c in ("unique", "primary", "check") for _, c in need)


def test_every_migration_column_is_in_the_schema_file():
    assert set(schema_check.migration_for()) <= schema_check.needed()
    assert schema_check.migration_for()[("callbacks", "note")] == "db/migrations/004_callback_note.sql"


def test_nothing_missing_is_schema_ok():
    result = schema_check.compare(schema_check.needed())
    assert result == {"missing": [], "migrations": []}
    assert schema_check.message(result).startswith("SCHEMA OK")


def test_missing_columns_name_the_migrations_to_run():
    have = schema_check.needed() - {("call_attempts", "phone_hash"), ("callbacks", "note")}
    result = schema_check.compare(have)
    assert result["missing"] == ["call_attempts.phone_hash", "callbacks.note"]
    assert result["migrations"] == ["db/migrations/003_number_check.sql",
                                    "db/migrations/004_callback_note.sql"]
    assert schema_check.message(result) == (
        "SCHEMA MISMATCH: missing call_attempts.phone_hash, callbacks.note, run the pending "
        "migrations: db/migrations/003_number_check.sql, db/migrations/004_callback_note.sql")


def test_a_missing_table_points_to_the_schema_file():
    have = {c for c in schema_check.needed() if c[0] != "call_turns"}
    result = schema_check.compare(have)
    assert "call_turns.text" in result["missing"] and result["migrations"] == ["db/schema.sql"]


def test_extra_database_columns_are_fine():
    assert schema_check.compare(schema_check.needed() | {("callbacks", "something_new")})["missing"] == []


@pytest.mark.parametrize("ok,test_mode,gate", [
    (False, False, "refuse"), (False, True, "memory"), (True, False, "go"), (None, False, "go"),
    (True, True, "go")])
def test_schema_gate(ok, test_mode, gate):
    assert main.schema_gate(ok, test_mode) == gate


class FakeConn:
    def __init__(self, have):
        self.have = have

    async def fetch(self, sql, *args):
        return [{"table_name": t, "column_name": c} for t, c in self.have]

    async def close(self):
        pass


def _fake_db(monkeypatch, have):
    async def connect(url):
        return FakeConn(have)
    monkeypatch.setattr(main.asyncpg, "connect", connect)
    monkeypatch.setattr(main.config, "DATABASE_URL", "postgresql://u:p@db.example.test/x")
    monkeypatch.setattr(main, "_schema_ok", False)


def test_a_mismatch_is_logged_at_error_on_every_check(monkeypatch, caplog):
    _fake_db(monkeypatch, schema_check.needed() - {("call_attempts", "phone_hash")})
    with caplog.at_level(logging.ERROR, logger="tam-voice-agent"):
        assert asyncio.run(main.check_schema()) is False
        assert asyncio.run(main.check_schema()) is False  # not cached: checked again
    lines = [r for r in caplog.records if r.getMessage().startswith("SCHEMA MISMATCH")]
    assert len(lines) == 2 and all(r.levelno == logging.ERROR for r in lines)
    assert "missing call_attempts.phone_hash" in lines[0].getMessage()


def test_a_match_is_remembered(monkeypatch):
    _fake_db(monkeypatch, schema_check.needed())
    assert asyncio.run(main.check_schema()) is True
    monkeypatch.setattr(main.asyncpg, "connect", None)  # not called again
    assert asyncio.run(main.check_schema()) is True


def test_an_unreachable_database_is_left_to_build_store(monkeypatch):
    _fake_db(monkeypatch, set())

    async def fail(url):
        raise OSError("down")
    monkeypatch.setattr(main.asyncpg, "connect", fail)
    assert asyncio.run(main.check_schema()) is None


@pytest.mark.parametrize("test_mode,code", [(False, 1), (True, None)])
def test_the_worker_refuses_to_start_on_a_mismatch_unless_test_mode(monkeypatch, test_mode, code):
    _fake_db(monkeypatch, schema_check.needed() - {("callbacks", "note")})
    monkeypatch.setattr(main.config, "TEST_MODE", test_mode)
    assert main.startup_schema_check(["main.py", "start"]) == code


def test_docker_build_download_files_does_not_touch_the_database(monkeypatch):
    monkeypatch.setattr(main, "check_schema", None)  # would fail if called
    assert main.startup_schema_check(["main.py", "download-files"]) is None


class FakeJob:
    metadata = '{"customer_id": "C002"}'


class FakeCtx:
    job = FakeJob()


def test_a_call_is_refused_on_a_mismatch_with_test_mode_off(monkeypatch, caplog):
    async def mismatch():
        return False

    async def no_store(customer_id):
        raise AssertionError("must not load data or start the call")
    monkeypatch.setattr(main, "check_schema", mismatch)
    monkeypatch.setattr(main, "build_store", no_store)
    monkeypatch.setattr(main.config, "TEST_MODE", False)
    with caplog.at_level(logging.ERROR, logger="tam-voice-agent"):
        assert asyncio.run(main.entrypoint(FakeCtx())) is None
    assert "refusing the call" in caplog.text


def _run_script(*args):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "check_schema.py"), *args],
                          capture_output=True, text=True, cwd=ROOT, timeout=60)


def test_check_schema_script_needs_an_env_file():
    out = _run_script()
    assert out.returncode == 1 and "Usage" in out.stdout


def test_check_schema_script_reports_a_missing_file_or_url(tmp_path):
    assert "not found" in _run_script(str(tmp_path / "nope.env")).stdout
    empty = tmp_path / "empty.env"
    empty.write_text("APP_ENV=staging\n", encoding="utf-8")
    out = _run_script(str(empty))
    assert out.returncode == 1 and "no DATABASE_URL" in out.stdout


@pytest.mark.parametrize("hour,ok", [("11", True), ("8", True), ("18", True), ("7", False),
                                     ("19", False)])
def test_default_callback_hour_must_be_inside_the_calling_window(hour, ok):
    env = {**os.environ, "DEFAULT_CALLBACK_HOUR": hour, "CALL_WINDOW_START": "8",
           "CALL_WINDOW_END": "19"}
    out = subprocess.run([sys.executable, "-c", "from tools import policy"], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=60)
    assert (out.returncode == 0) is ok
    if not ok:
        assert "DEFAULT_CALLBACK_HOUR must be inside the calling window" in out.stderr


# ======================= 2. day-only callbacks =======================

def ask():
    return TimeAsk(lambda: NOW)


DAY_ONLY = "caller gave day only"
ANY_TIME = "caller said any time"


def test_a_day_alone_asks_once_for_the_time():
    a = ask()
    assert a.read("kal") == {"ask_time": date(2026, 10, 29)}
    assert a.read("pata nahi kab") == {"when": THURSDAY_11, "day_only": True, "note": DAY_ONLY}


@pytest.mark.parametrize("second", ["pata nahi", "nahi pata", "dekhte hain", "hmm"])
def test_no_time_after_asking_books_the_default_hour(second):
    a = ask()
    assert a.read("Monday") == {"ask_time": date(2026, 11, 2)}
    assert a.read(second) == {"when": MONDAY_11, "day_only": True, "note": DAY_ONLY}


@pytest.mark.parametrize("second", ["koi bhi time", "jab marzi", "कोई भी टाइम", "any time"])
def test_any_time_after_asking_books_the_default_hour(second):
    a = ask()
    a.read("Monday")
    assert a.read(second) == {"when": MONDAY_11, "day_only": True, "note": ANY_TIME}


@pytest.mark.parametrize("text", [
    "any time Monday", "Monday anytime", "call me Monday, whenever", "Monday koi bhi time",
    "किसी भी time call कर लेना Monday", "Monday kabhi bhi", "jab chahe Monday", "Monday जब चाहे",
    "Monday any time is fine", "सोमवार कभी भी"])
def test_any_time_with_a_day_books_at_once(text):
    assert ask().read(text) == {"when": MONDAY_11, "day_only": True, "note": ANY_TIME}


def test_any_time_then_the_day_in_the_next_answer():
    a = ask()
    assert a.read("whenever") == {"missing": "both"}  # no day yet: the flow asks for it
    assert a.read("Monday") == {"ask_time": date(2026, 11, 2)}


def test_the_time_is_asked_once_even_when_the_flow_asked_it():
    a = ask()
    a.read("kal shaam")  # the flow said "please tell me the time"
    a.mark_asked()
    assert a.read("pata nahi") == {"when": THURSDAY_11, "day_only": True, "note": DAY_ONLY}


def test_a_time_after_asking_is_for_that_day():
    a = ask()
    a.read("parson")
    assert a.read("shaam 5 baje") == {"when": datetime(2026, 10, 30, 17, 0, tzinfo=IST)}


def test_a_new_day_after_asking_moves_the_default_booking():
    a = ask()
    a.read("kal")
    assert a.read("nahi, Monday") == {"when": MONDAY_11, "day_only": True, "note": DAY_ONLY}


def test_a_day_with_a_part_of_the_day_asks_for_the_hour():
    assert ask().read("kal shaam") == {"ask_time": date(2026, 10, 29)}


@pytest.mark.parametrize("text", ["20 November", "aaj", "20 November koi bhi time"])
def test_not_day_only_keeps_the_usual_handling(text):
    # too far; today, when 11 am has passed (it is 11:00 now)
    assert ask().read(text) == {"missing": "time"}


def test_a_full_time_is_booked_as_said():
    assert ask().read("kal shaam 6 baje") == {"when": datetime(2026, 10, 29, 18, 0, tzinfo=IST)}


TIME_FOR_THURSDAY = says(caller_words.TIME_FOR_DAY, day="Thursday 29 October")
TIME_FOR_MONDAY = says(caller_words.TIME_FOR_DAY, day="Monday 2 November")


def _callbacks(store):
    return [(c["reason"], c["due_at"], c["note"]) for c in store.callbacks_due(FAR)]


def test_third_party_day_only(caplog):
    agent, call, store, session = make_agent()
    hear(agent, "Sunita ghar pe nahi hai")
    hear(agent, "main unki behen hoon")
    hear(agent, "nahi")  # no consent: asked when to reach Sunita
    with caplog.at_level(logging.INFO, logger="tam-voice-agent"):
        assert hear(agent, "kal") is True
        assert session.said[-1] == TIME_FOR_THURSDAY and session.closed is False
        assert hear(agent, "pata nahi") is True
    assert session.said[-1] == says(caller_words.TP_CLOSE_DAY, day="Thursday 29 October")
    assert "बजे" not in session.said[-1] and session.closed is True
    assert _callbacks(store) == [("customer_unavailable", THURSDAY_11, "caller gave day only")]
    assert call.outcome == "third_party" and call.note == "caller gave day only"
    assert "RULE callback_time_ask" in caplog.text and "RULE callback_day_only" in caplog.text


def test_relative_cant_commit_day_only():
    agent, call, store, session = make_agent()
    verified_brother(agent)
    hear(agent, "pata nahi, unse hi poochna padega")
    hear(agent, "kal")
    assert session.said[-1] == TIME_FOR_THURSDAY
    hear(agent, "pata nahi kab")
    assert session.said[-1] == says(caller_words.TP_CLOSE_DAY, day="Thursday 29 October")
    assert _callbacks(store) == [("customer_unavailable", THURSDAY_11, "caller gave day only")]
    assert call.note == "relative could not commit, callback booked; caller gave day only"
    assert store.promises_for("C002") == []


def test_relative_day_then_time_books_that_time():
    agent, call, store, session = make_agent()
    verified_brother(agent)
    hear(agent, "pata nahi")
    hear(agent, "Monday")
    hear(agent, "dopahar 3 baje")
    assert _callbacks(store) == [("customer_unavailable", datetime(2026, 11, 2, 15, 0, tzinfo=IST), None)]
    assert call.note == "relative could not commit, callback booked"


def test_hardship_day_only():
    agent, call, store, session = make_agent()
    confirmed(call)
    hear(agent, "meri job chali gayi")
    hear(agent, "abhi pata nahi")  # no date: a colleague will call
    assert session.said[-1] == says(caller_words.HARDSHIP_CALLBACK)
    hear(agent, "Monday")
    assert session.said[-1] == TIME_FOR_MONDAY and session.closed is False
    hear(agent, "koi bhi time")
    assert session.said[-1] == says(caller_words.HANDOFF_CLOSE_DAY, day="Monday 2 November")
    assert session.closed is True
    assert _callbacks(store) == [("human_followup", MONDAY_11, "caller said any time")]
    assert call.outcome == "hardship" and call.note.endswith("caller said any time")


def test_hardship_pata_nahi_without_a_day_still_closes_without_a_time():
    agent, call, store, session = make_agent()
    confirmed(call)
    hear(agent, "meri job chali gayi")
    hear(agent, "abhi pata nahi")
    hear(agent, "pata nahi")
    assert session.said[-1] == says(caller_words.HANDOFF_CLOSE_NO_TIME)


def test_customer_busy_day_only(caplog):
    agent, call, store, session = make_agent()
    with caplog.at_level(logging.INFO, logger="tam-voice-agent"):
        hear(agent, "haan, abhi busy hoon")
        assert session.said == [caller_words.CALL_LATER_ASK["hi"]]
        hear(agent, "Monday call karna")
        assert session.said[-1] == TIME_FOR_MONDAY
        assert hear(agent, "koi bhi time") is True
    assert session.said[-1] == says(caller_words.CALL_LATER_CLOSE_DAY, day="Monday 2 November")
    assert session.closed is True
    assert _callbacks(store) == [("customer_requested", MONDAY_11, "caller said any time")]
    assert call.outcome == "callback_requested" and call.note == "caller said any time"
    assert not call.unlocked and "RULE call_later_close" in caplog.text


def test_customer_busy_with_a_day_and_time_is_booked_at_once():
    agent, call, store, session = make_agent()
    hear(agent, "haan ji, abhi busy hoon")
    hear(agent, "kal subah 10 baje call karna")
    assert session.said[-1] == says(caller_words.CALL_LATER_CLOSE, when="Thursday 29 October, सुबह 10 बजे")
    assert _callbacks(store) == [("customer_requested", datetime(2026, 10, 29, 10, 0, tzinfo=IST), None)]
    assert call.outcome == "callback_requested"


def test_customer_busy_with_a_day_too_far_gets_the_usual_question():
    agent, call, store, session = make_agent()
    hear(agent, "haan, abhi busy hoon")
    hear(agent, "20 November ko call karna")
    assert session.said[-1] == caller_words.TIME_INCOMPLETE["hi"] and store.callbacks_due(FAR) == []


def test_verified_customer_asking_mid_call_to_be_called_later():
    agent, call, store, session = make_agent()
    assert hear(agent, "haan ji") is True  # the reminder
    assert hear(agent, "abhi baat nahi kar sakti, baad mein call karna") is True
    assert session.said[-1] == caller_words.CALL_LATER_ASK["hi"]
    hear(agent, "parson")
    hear(agent, "pata nahi")
    assert _callbacks(store) == [("customer_requested", datetime(2026, 10, 30, 11, 0, tzinfo=IST),
                                  "caller gave day only")]


def test_the_ai_reporting_unavailable_hands_the_time_to_the_code():
    agent, call, store, session = make_agent()
    call.heard("abhi nahi, baad mein")
    assert run_tool(agent, agent.report_person(None, "unavailable")) == "stopped"
    assert session.said == [caller_words.CALL_LATER_ASK["hi"]] and agent.later.active


def test_call_later_in_english():
    agent, call, store, session = make_agent("C003")
    agent.language.current = "en"
    hear(agent, "yes, but I'm busy right now")
    hear(agent, "call me Monday")
    assert session.said[-1] == "What time on Monday 2 November would suit?"
    hear(agent, "any time")
    assert session.said[-1] == "All right, Amit. We'll call you on Monday 2 November. Thank you, have a good day."


@pytest.mark.parametrize("text,yes", [
    ("abhi busy hoon", True), ("baad mein call karna", True), ("Monday call karo", True),
    ("call me later", True), ("अभी बात नहीं कर सकती", True), ("बाद में कॉल करना", True),
    ("baad mein pay kar dungi", False), ("busy hoon, Friday tak de dungi", False),
    ("main call karungi", False), ("haan bataiye", False)])
def test_what_counts_as_asking_to_be_called_later(text, yes):
    assert asks_call_later(text) is yes


def test_the_ai_cannot_book_a_day_only_callback():
    agent, call, store, session = make_agent()
    confirmed(call)
    call.heard("Monday")
    r = run_tool(agent, agent.schedule_callback(None, 2, 11, 2026, 11, 0, "customer_requested"))
    assert r["saved"] is False and r["reason"] == "caller_did_not_give_a_time"


# ======================= 3. fuzzy names need a yes =======================

# per mock customer: (the name exactly, in another spelling or script) and a close name
EXACT = {
    "Rahul": ["Rahul", "राहुल", "Rahool"],
    "Sunita": ["Sunita", "सुनीता", "Suneeta", "Sunitha"],
    "Amit": ["Amit", "अमित", "Ameet"],
    "Priya": ["Priya", "प्रिया", "Pria"],
    "Subramaniam": ["Subramaniam", "सुब्रमण्यम", "Subramanyam"],
    "कविता": ["कविता", "Kavita", "Kavitha"],
    "Mohammed": ["Mohammed", "मोहम्मद", "Mohammad"],
    "Neha": ["Neha", "नेहा", "Nehaa"],
    "Deepak": ["Deepak", "दीपक", "Dipak"],
    "Anil": ["Anil", "अनिल", "Aneel"],
}
FUZZY = {"Rahul": "Rahil", "Sunita": "Sanita", "Amit": "Amita", "Priya": "Prita",
         "Subramaniam": "Subramanian", "कविता": "Savita", "Mohammed": "Muhammad",
         "Neha": "Nehal", "Deepak": "Deepa", "Anil": "Anila"}
CUSTOMER = {"Rahul": "C001", "Sunita": "C002", "Amit": "C003", "Priya": "C004",
            "Subramaniam": "C006", "कविता": "C007", "Mohammed": "C008", "Neha": "C009",
            "Deepak": "C010", "Anil": "C011"}


def test_every_mock_name_is_covered():
    from tools.store import load_company_data
    customers, _ = load_company_data()
    assert {c["name"].split()[0] for c in customers} == set(EXACT) == set(FUZZY)


@pytest.mark.parametrize("name,word", [(n, w) for n, ws in EXACT.items() for w in ws])
def test_exact_and_transliterated_names_confirm_alone(name, word):
    assert names.exact_name(word, name)
    assert caller_words.confirms_identity(word, name)
    assert caller_words.confirms_identity(f"{word} बोल रही हूँ", name)
    assert caller_words.confirms_identity(f"{word} bol raha hoon", name)


@pytest.mark.parametrize("name,word", FUZZY.items())
def test_a_fuzzy_name_alone_does_not_confirm(name, word):
    assert names.same_name(word, name) and not names.exact_name(word, name)
    for text in (word, f"{word} bol rahi hoon", f"{word} speaking", f"I am {word}", f"{word} जी"):
        assert not caller_words.confirms_identity(text, name), text


@pytest.mark.parametrize("name,word", FUZZY.items())
def test_a_fuzzy_name_with_a_yes_confirms(name, word):
    for text in (f"haan, {word} bol rahi hoon", f"ji, {word}", f"yes, {word} speaking",
                 f"हाँ जी, {word} बोल रही हूँ", f"main hi {word} hoon"):
        assert caller_words.confirms_identity(text, name), text


def test_amit_and_amita_both_ways():
    assert not caller_words.confirms_identity("Amita bol rahi hoon", "Amit")
    assert caller_words.confirms_identity("Amit bol raha hoon", "Amit")
    assert not caller_words.confirms_identity("Amit bol raha hoon", "Amita")


@pytest.mark.parametrize("name,word", FUZZY.items())
def test_a_fuzzy_name_alone_does_not_unlock_the_call(name, word, caplog):
    agent, call, store, session = make_agent(CUSTOMER[name])
    with caplog.at_level(logging.INFO, logger="tam-voice-agent"):
        hear(agent, f"{word} bol rahi hoon")
    assert call.person_status is None and not call.unlocked
    assert session.said == [agent._identity_question()]
    assert "RULE identity_fuzzy_name" in caplog.text
    # the AI can't unlock it either
    assert run_tool(agent, agent.report_person(None, "customer")) == "stopped"
    assert not call.unlocked


@pytest.mark.parametrize("name,word", FUZZY.items())
def test_a_fuzzy_name_with_a_yes_unlocks_the_call(name, word):
    agent, call, store, session = make_agent(CUSTOMER[name])
    assert hear(agent, f"haan, {word} bol rahi hoon") is True
    assert call.unlocked and call.person_status == "customer"


def test_suneeta_with_a_yes_unlocks_sunita():
    agent, call, store, session = make_agent()
    assert hear(agent, "haan, Suneeta bol rahi hoon") is True
    assert call.unlocked


def test_after_a_fuzzy_name_a_yes_unlocks():
    agent, call, store, session = make_agent("C003")
    hear(agent, "Amita bol rahi hoon")
    assert not call.unlocked
    assert hear(agent, "yes") is True and call.unlocked
