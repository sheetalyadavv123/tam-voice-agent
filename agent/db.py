import json
from datetime import datetime, timezone

import asyncpg

import config


def extract_turns(history):
    """Pull user/assistant messages out of the session's chat history."""
    turns = []
    for item in history.items:
        if getattr(item, "type", None) != "message":
            continue
        if item.role not in ("user", "assistant"):
            continue
        text = item.text_content
        if not text:
            continue
        turns.append({
            "role": item.role,
            "text": text,
            "interrupted": bool(getattr(item, "interrupted", False)),
            "created_at": datetime.fromtimestamp(item.created_at, tz=timezone.utc),
        })
    return turns


async def save_call(session_id, room_name, channel, started_at, ended_at, turns,
                    outcome=None, metadata=None):
    duration = int((ended_at - started_at).total_seconds())
    conn = await asyncpg.connect(config.DATABASE_URL)
    try:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO call_sessions
                    (id, room_name, channel, started_at, ended_at, duration_seconds,
                     turn_count, outcome, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
                ON CONFLICT (id) DO UPDATE SET
                    ended_at = EXCLUDED.ended_at,
                    duration_seconds = EXCLUDED.duration_seconds,
                    turn_count = EXCLUDED.turn_count,
                    outcome = EXCLUDED.outcome
                """,
                session_id, room_name, channel, started_at, ended_at, duration,
                len(turns), outcome, json.dumps(metadata or {}),
            )
            await conn.executemany(
                """
                INSERT INTO call_turns
                    (session_id, turn_index, role, text, interrupted, created_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (session_id, turn_index) DO NOTHING
                """,
                [(session_id, i, t["role"], t["text"], t["interrupted"], t["created_at"])
                 for i, t in enumerate(turns)],
            )
    finally:
        await conn.close()