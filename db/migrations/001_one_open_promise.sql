-- At most one open promise per customer.
-- 1. Keep only the newest open promise per customer; cancel the rest.
-- 2. Replace the (customer_id, promised_date) unique index with one on customer_id.
-- Safe to run twice. Run with: python scripts/migrate.py db/migrations/001_one_open_promise.sql
BEGIN;

UPDATE promises p SET status = 'cancelled'
WHERE p.status = 'open'
  AND EXISTS (
      SELECT 1 FROM promises n
      WHERE n.customer_id = p.customer_id AND n.status = 'open'
        AND (n.created_at, n.id) > (p.created_at, p.id));

DROP INDEX IF EXISTS uq_open_promise;
CREATE UNIQUE INDEX IF NOT EXISTS uq_open_promise_per_customer
    ON promises (customer_id) WHERE status = 'open';

COMMIT;
