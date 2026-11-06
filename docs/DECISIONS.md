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

## 2026-11-02: Relative pay dates, and a date beyond the limit

A Console caller said "मैं इसको दस दिन बाद pay कर दूंगी" and got only "हमारी एक सहयोगी
आपको वापस कॉल करेंगी", with no explanation.

- **Relative dates are worked out by the code (IST)**, like weekdays, and added to the
  turn ("दस दिन बाद = Thursday 12 November"):
  - "10 din baad", "दस दिन बाद", "in 10 days": today + 10.
  - "ek hafte mein", "in a week": + 7. "2 hafte baad", "two weeks": + 14.
  - "agle hafte", "next week": today + 7. With a weekday ("agle hafte Friday"), that
    weekday in the next calendar week.
  - "mahine ke end tak", "month end": the last day of this month.
  - A day of the month ("12 को", "9 तक", "15 tareekh", "by the 12th"): that day this
    month, or next month once it has passed.
- **The limit is `MAX_PROMISE_DAYS`** (default 7, already used for callbacks): the last
  allowed pay date is today + MAX_PROMISE_DAYS.
- **Too far: one try for earlier, in code.** "ठीक है, {date} नोट किया। क्या आप {latest} तक
  pay कर पाएंगे?" A date within the limit, or a plain yes (= the last allowed day), is
  recorded as a promise as usual and the AI carries on (amount, date, link).
- **Still too far:** replaced on 2026-11-03, see the next entry. (At first the code said
  a colleague would confirm the date and asked for a day and time for their call.)
- If the AI's own record_promise_to_pay gets "too far", the code takes over with the same
  question. The AI never says "a colleague will call" about a date.
- "10 din baad call karna" is a call-later request, not a pay date.

**Where.** `tools/relative_dates.py`, `tools/far_date.py`, `tools/collection_tools.py`
(`far_date_handoff`), `tools/callback_ask.py` (`next_default_slot`), `agent/main.py`
(`_add_days_note`, `_far_date_start`, `_far_reply`).

## 2026-11-03: A date still too far ends the call (from a Console test)

**Decision.** The one try for an earlier date stays: "ठीक है, {date} नोट किया। क्या आप
{latest} तक pay कर पाएंगे?". Then:

- **A date within the limit, or a plain yes** (= the last allowed day): the promise is
  saved as usual.
- **Still too far** (the same or another far date, a no, "nahi ho payega"): the code
  speaks one fixed line and the call ends. There is no question about a day or time:
  - Hindi: "ठीक है, {date} थोड़ा आगे की तारीख है। इसके बारे में हमारी टीम से कोई आपसे बात
    करेगा। धन्यवाद, आपका दिन शुभ हो।"
  - English: "All right, {date} is a bit further out than I can note. Someone from our
    team will talk to you about it. Thank you, have a good day."
  - A verified relative hears "... {name} जी से बात करेगा".

  A human_followup callback is booked at the next DEFAULT_CALLBACK_HOUR slot inside the
  calling hours: today's if it is still ahead, else tomorrow's. The note on the call and
  the callback is "customer offered {date}, beyond limit" (the last far date said), and
  the outcome is human_callback.
- **An unclear answer is not a no.** An answer that can't be read (empty, garbled,
  another script such as "నైన్" or "ఆండీ", or no yes, no no and no date) gets the same
  question once more. A second unclear answer gets the close.

**Where.** `tools/far_date.py` (`answer`, `close`), `tools/caller_words.py`
(`FAR_DATE_CLOSE`), `tools/collection_tools.py` (`far_date_handoff`).

## 2026-11-03: Hardship and a pay date; ranges

**Rule.** Hardship with a date within the limit: no colleague (founder). Beyond the
limit: human follow-up, no push for an earlier date (pending founder confirmation).

- **Ranges.** "10-15 din", "10 se 15 din mein", "10 to 15 days", "2-3 hafte" resolve to a
  first and a last day; "agle mahine", "next month" is the whole of next month. The
  limit check uses the later day. The turn's note and the call's note keep both
  ("10-15 din mein = Friday 13 November to Wednesday 18 November"; "offered 13 to 18
  November"). A promise from a range is for its last day.
- **Hardship + a date within the limit:** unchanged. The promise is saved and no
  colleague is mentioned. The speech guard drops colleague / call-back talk only in this
  case (`call.hardship == "with_date"`).
- **Hardship + a date beyond the limit:** there is no earlier-date question. The code says
  a fixed line and ends the call, with no time question:
  - Hindi: "मैं समझ सकती हूँ, {name} जी। आपने जो {date} बताया है, उसके बारे में हमारी टीम से
    कोई आपसे बात करेगा। अपना ध्यान रखिए, धन्यवाद।"
  - English: "I understand, {name}. Someone from our team will talk to you about {date},
    as you mentioned. Take care, thank you."

  A human_followup callback is booked at the next DEFAULT_CALLBACK_HOUR slot. The outcome
  is hardship, with the note "hardship, offered {range or date}, beyond limit". This
  applies wherever the date comes: in the hardship message itself, after the code's "by
  which date?", later in the call, or from the AI's too-far record_promise_to_pay.

**Where.** `tools/relative_dates.py` (`RANGE`, `NEXT_MONTH`, `Offer`, `say_offer`),
`tools/far_date.py` (`hardship_close`, `book_followup`), `tools/caller_words.py`
(`HARDSHIP_FAR_CLOSE`), `agent/main.py` (`_paths`, `_hardship_far`).

## 2026-11-03: One order for the rules; hardship without a date

**Rule.** Once the call is unlocked, the order in which the rules take a message is
decided in one place, `tools/precedence.py` (`route`), and `agent/main.py` (`_paths`)
runs the result:

    legal -> wrong number -> hardship beyond the limit -> plain beyond the limit
          -> hardship within the limit -> normal promise -> hardship with no date

- **Everywhere.** The order applies to every message, including while the code is asking
  for an earlier date. A legal question or "wrong number" there gets its own close, not
  the earlier-date question again. The AI's too-far `record_promise_to_pay` also goes
  through the same order.
- **Hardship inside the too-far flow.** Hardship said at any point while the code asks
  for an earlier date switches to the hardship-beyond-limit close, using the date
  already offered or a new far one. The one exception follows the order: an earlier date
  within the limit, given with the hardship, saves the promise (hardship within the
  limit).
- **A later far date after a hardship promise within the limit.** The promise is
  cancelled, the no-colleague rule ends (`call.hardship` becomes `far_date`), and the
  hardship-beyond-limit close is spoken. Without hardship, changing a saved promise stays
  with the AI.
- **Hardship without any date.** The code asks once for a date. If none comes, there is
  no day or time question: the code says "मैं समझ सकती हूँ। इसके बारे में हमारी टीम से कोई
  आपसे बात करेगा। अपना ध्यान रखिए, धन्यवाद।" and the call ends. It books a human_followup
  callback at the next DEFAULT_CALLBACK_HOUR slot, with outcome hardship and note
  "hardship, no pay date". This replaces the earlier "a colleague will call: what day and
  time?".
- **A verified relative** reporting hardship gets the same paths in the third person
  ("Sunita जी किस तारीख तक pay कर पाएंगे?", "हमारी टीम से कोई Sunita जी से बात करेगा").
- **Hardship said indirectly** also counts: "ghar mein problem chal rahi hai", "abhi
  haalat theek nahi hai", "things are tough right now", "going through a tough time",
  "paise ki dikkat".
- **After a dispute** (the payment push is over), only legal and wrong number apply.

**Where.** `tools/precedence.py`, `agent/main.py` (`_paths`, `_hardship_far`,
`_hardship_no_date`), `tools/far_date.py` (`hardship_no_date_close`),
`tools/collection_tools.py` (`withdraw_promise`), `tools/caller_words.py` (`HARDSHIP`,
`HARDSHIP_DATE_ASK_TP`, `HARDSHIP_NO_DATE_CLOSE`).

## 2026-11-06: SAMPARK calls: the room is made at /sampark; close code 1011 is a failed call

**Decision.** The bridge creates the call's LiveKit room (which dispatches the agent) when
the dialer calls `/sampark`, so the room and the agent's start overlap with the dialer
connecting. One LiveKit API client serves the whole bridge process.

- `/sampark` waits up to `ROOM_WAIT` (1.0 s) for the room. A creation that fails within
  that time gets an HTTP 502 and no URL. Room creation usually takes about 1.6 s, so most
  of the time the URL goes out before the result is known.
- The WebSocket handler waits for the room if it isn't ready yet. If creation fails
  after the URL went out, the bridge closes the WebSocket with **code 1011** without
  sending any event.
- A URL that isn't used within 30 s has its room removed, so the agent doesn't wait in an
  empty call.

**Rule (dialer contract).** The dialer must treat a WebSocket closed with code 1011 as a
**failed call** (the bot never answered), not as a call that ended: retry or mark it
failed. The other closes: 4401 is an unknown, used or expired URL; a `stop` event with
reason `callended` before the close is a normal end.

**Rule (agent).** The greeting waits until the caller is in the room, because the room can
now exist before the caller joins and the agent's `session.start()` doesn't wait for
anyone. Console mode doesn't wait.

- The wait is `CALLER_WAIT_SECONDS` (25 s), below the bridge's 30 s: the agent's wait starts
  seconds after `/sampark`, and the bridge deletes an unused room 30 s after it.
- Nobody comes: no greeting, the job ends, and the call is saved with the outcome
  **`caller_never_joined`** and the note "caller never joined the room". The same happens
  when the room is deleted first (a slow start): the job's shutdown hook, registered before
  the wait, records it. The call record is saved even though it has no turns.
- `caller_never_joined` is set by the code only; the AI can't choose it. It is not
  `not_available`, which means the customer couldn't talk, so reports keep the two apart.
  The attempt still counts toward the daily and weekly limits (those count every attempt,
  whatever its outcome; the phone may have rung). It doesn't lift a wrong-number hold: the
  hold looks at the latest attempt with an outcome other than `caller_never_joined`.

**Why.** In a live test the room took 3.2 s to create after the dialer connected, all of
it on the call's critical path. Created at `/sampark`, it is ready before the dialer
connects (first agent audio 4.2 to 5.2 s after `/sampark`, from about 6.5 s). Waiting for
the result in `/sampark` would add about 0.6 s to every call to report a rare failure, so
the late failure is reported on the WebSocket instead.

**Where.** `bridge/app.py` (`ROOM_WAIT`, `sampark`, `media_ws`, `expire`), `bridge/rooms.py`,
`agent/main.py` (`wait_for_caller`, `CALLER_WAIT_SECONDS`, `finish`), `tools/store.py`
(`NO_CALLER`, `number_check_hold`).
