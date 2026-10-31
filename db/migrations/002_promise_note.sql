-- Who made a promise, when it was not the customer: "promised by brother Rahul".
-- NULL for the customer's own promises. Safe to run twice.
-- Run with: python scripts/migrate.py db/migrations/002_promise_note.sql
BEGIN;

ALTER TABLE promises ADD COLUMN IF NOT EXISTS note TEXT;

COMMIT;
