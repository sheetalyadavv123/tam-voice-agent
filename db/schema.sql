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