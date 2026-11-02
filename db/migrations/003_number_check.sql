-- Wrong-number hold. After a wrong-number call the scheduler stops calling the customer
-- until a person checks the number (scripts/clear_number_check.py sets number_checked_at)
-- or the phone number in the company records changes (phone_hash no longer matches).
-- phone_hash: SHA-256 of the dialled number's digits, so the number itself isn't copied.
-- Safe to run twice. Run with: python scripts/migrate.py db/migrations/003_number_check.sql
BEGIN;

ALTER TABLE call_attempts ADD COLUMN IF NOT EXISTS phone_hash TEXT;
ALTER TABLE call_attempts ADD COLUMN IF NOT EXISTS number_checked_at TIMESTAMPTZ;

COMMIT;
