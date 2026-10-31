CREATE TABLE IF NOT EXISTS call_sessions (
    id UUID PRIMARY KEY,
    room_name TEXT,
    channel TEXT NOT NULL DEFAULT 'console',
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    duration_seconds INT,
    turn_count INT,
    outcome TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS call_turns (
    id BIGSERIAL PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES call_sessions(id) ON DELETE CASCADE,
    turn_index INT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    text TEXT NOT NULL,
    interrupted BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ,
    UNIQUE (session_id, turn_index)
);

CREATE INDEX IF NOT EXISTS idx_turns_session ON call_turns (session_id);

CREATE TABLE IF NOT EXISTS call_attempts (
    id BIGSERIAL PRIMARY KEY,
    customer_id TEXT NOT NULL,
    attempted_at TIMESTAMPTZ NOT NULL,
    session_id UUID,
    kind TEXT NOT NULL DEFAULT 'reminder' CHECK (kind IN ('reminder', 'followup')),
    outcome TEXT
);
CREATE INDEX IF NOT EXISTS idx_attempts_customer ON call_attempts (customer_id, attempted_at);

CREATE TABLE IF NOT EXISTS promises (
    id BIGSERIAL PRIMARY KEY,
    customer_id TEXT NOT NULL,
    promised_date DATE NOT NULL,
    amount INT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'kept', 'broken', 'cancelled')),
    session_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- One open promise per customer. Older databases: db/migrations/001_one_open_promise.sql
DROP INDEX IF EXISTS uq_open_promise;
CREATE UNIQUE INDEX IF NOT EXISTS uq_open_promise_per_customer
    ON promises (customer_id) WHERE status = 'open';

CREATE TABLE IF NOT EXISTS payment_links (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    amount INT NOT NULL,
    url TEXT NOT NULL,
    channel TEXT NOT NULL CHECK (channel IN ('sms', 'whatsapp')),
    status TEXT NOT NULL DEFAULT 'sent' CHECK (status IN ('sent', 'paid', 'failed', 'expired')),
    session_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    paid_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS callbacks (
    id BIGSERIAL PRIMARY KEY,
    customer_id TEXT NOT NULL,
    due_at TIMESTAMPTZ NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done', 'cancelled')),
    session_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);