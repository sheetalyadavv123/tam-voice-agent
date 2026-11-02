-- Why a callback has the time it has: "caller gave day only" when the caller named a day
-- but no time, so the code booked DEFAULT_CALLBACK_HOUR on that day. NULL otherwise.
-- Safe to run twice. Run with: python scripts/migrate.py db/migrations/004_callback_note.sql <env file>
BEGIN;

ALTER TABLE callbacks ADD COLUMN IF NOT EXISTS note TEXT;

COMMIT;
