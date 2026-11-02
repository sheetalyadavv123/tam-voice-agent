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

## 2026-11-02: Busy callers (from a Console test)

A caller answered the greeting with "Actually, I'm quite occupied ... can you please call
me on Monday?". The code read "I'm" as a yes and gave the EMI reminder; the AI then asked
for an exact time four times and ended with "a colleague will call you", booking nothing.

- **"I'm" is not a yes.** "I'm", "I am" and "this is" confirm identity only when the
  customer's name follows (exact or transliterated). "I'm busy", "I'm occupied",
  "I'm driving" never do. "Speaking", "main hi hoon" and "bol rahi hoon" still do.
- **Busy before any yes.** If the first answer only says busy / occupied / call me later /
  call me on a day, with no clear yes, the code asks "am I speaking with Sunita?" once.
  A clear yes goes into the code's call-later flow. Someone else goes into the
  third-party flow. Anything else: the callback is booked anyway, as
  customer_unavailable, and nothing about the loan is shared (outcome not_available,
  note "identity not confirmed, callback booked"). Both answers are read for the day.
- **More call-later phrases.** "I'm busy", "I'm occupied", "not a good time", "call me
  later / tomorrow / on Monday", "can you call back", "abhi time nahi". "abhi nahi" and
  "baad mein" count only at the greeting; mid-call they answer "when will you pay?".
- **"Any time" is an answer.** "any time", "anytime", "whenever", "koi bhi time",
  "किसी भी time", "kabhi bhi", "jab chahe" with a day books that day at
  DEFAULT_CALLBACK_HOUR at once, note "caller said any time".
- **The time is asked once.** One question for the time per flow (the flow's own "please
  tell me the day and time" counts); after that, an answer with a day but no time books
  the default hour.
- **No colleague for a busy customer.** The reply guard drops any sentence about a
  colleague / सहयोगी once the customer has asked to be called later. The close says
  "we'll call you on <day>".

**Where.** `tools/caller_words.py` (`SPEAKING`), `tools/call_later.py`
(`busy_before_identity`, `GREETING_CALL_LATER`), `tools/callback_ask.py` (`ANY_TIME`,
`mark_asked`), `tools/speech_guard.py` (`no_colleague`), `agent/main.py`
(`_after_busy`, `_guard`).
