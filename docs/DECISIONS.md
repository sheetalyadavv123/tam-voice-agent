# Decisions

Decisions about how the agent behaves, and the rules they set. Newest last. Each one
says what was decided, why, and where it lives in the code.

## 2026-11-02: The database schema is checked before taking calls

**Decision.** At startup the agent compares the tables and columns the code needs
(everything `db/schema.sql` creates) with the database. If anything is missing it logs
`SCHEMA MISMATCH: missing <table.column>, run the pending migrations: <files>` at ERROR.

- `TEST_MODE` off: the agent refuses to take calls. The worker exits with code 1 before
  it registers, and each call checks again before saying anything (a passing check is
  remembered per process).
- `TEST_MODE` on: calls go ahead with the in-memory data (nothing is saved to the
  database; the transcript is written to `transcripts/`), and the mismatch is logged on
  every call.
- If the database can't be reached, the check is skipped and the existing rule applies
  (no call without the database unless `TEST_MODE` is on).

**Rule.** Every deploy follows README, *Deploying*: merge, run every new migration on
staging, run `scripts/check_schema.py <staging env file>` until it prints `SCHEMA OK`,
then deploy. Every schema change is a new numbered file in `db/migrations/` and is also
added to `db/schema.sql`, which is what the check reads.

**Why.** Migration 003 was merged without being run anywhere. Code that writes a column
the database doesn't have fails at the end of the call, when the call is saved, so the
call's promise, callback and outcome would be lost.

**Where.** `tools/schema_check.py`, `agent/main.py` (`check_schema`, `schema_gate`,
`startup_schema_check`), `scripts/check_schema.py`.

## 2026-11-02: A day without a time books the default hour

**Decision.** In every callback flow (someone else answered, a relative who can't commit,
hardship, a customer who asks to be called later), a day with no time ("kal", "Monday",
"parson") gets one question for the time. If the next answer still has no time, the call
is booked on that day at `DEFAULT_CALLBACK_HOUR` (default 11, must be inside the calling
window) with the note "caller gave day only" on the callback and the call. The close
names the day only. A day outside the allowed days, too far ahead, or with a part of the
day but no hour ("kal shaam") gets the usual handling.

**Where.** `tools/callback_ask.py`, `tools/call_later.py`, `tools/third_party.py`,
`agent/main.py`; the note is `callbacks.note` (migration 004).

## 2026-11-02: A name close to the customer's needs a yes

**Decision.** The customer's name, in any spelling or script ("Sunita", "Suneeta",
"सुनीता"; "Mohammad", "मोहम्मद" for Mohammed) confirms identity on its own. A name a
letter or two off ("Amita" for Amit, "Muhammad" for Mohammed) only counts with a clear
yes ("haan", "ji", "yes") or "main hi" / "it's me" in the same message. Alone, the code
asks "am I speaking with Amit?" again (twice at most) and the AI can't unlock the call.

**Where.** `tools/names.py` (`exact_name`, `fuzzy_name`), `tools/caller_words.py`
(`confirms_identity`), `agent/main.py` (`_identity_reply`).
