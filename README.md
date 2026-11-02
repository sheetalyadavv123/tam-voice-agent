# tam-voice-agent

A Hindi/English voice agent for EMI collection calls, built on LiveKit Agents. The code
decides and speaks fixed lines wherever it can (who answered, the third-party check,
hardship, callbacks, wrong numbers); the AI (gpt-5.4-mini) handles the rest of the
conversation, and its tools are checked against the caller's own words.

- `agent/main.py`: the LiveKit worker and the `Collector` agent
- `tools/`: rules, the caller's words, the third-party flow, the stores
- `db/schema.sql`, `db/migrations/`: the database (Neon Postgres)
- `scripts/`: one-off tools (migrations, schema check, follow-ups, demo data)
- `docs/DECISIONS.md`: decisions and the rules they set

## Running locally

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env          (then fill it in; never commit it)
.venv\Scripts\python agent\main.py console
```

Tests: `pytest -q`. The rolled-back Neon tests also run with `RUN_DB_TESTS=1`.

## Deploying

Every step uses the env file for the environment you are deploying to; nothing here reads
`.env` unless you pass it.

1. **Merge** the pull request into `main` once the tests pass.
2. **Run every new migration on staging**, oldest first, for each file in `db/migrations/`
   that staging doesn't have yet:
   ```
   python scripts/migrate.py db/migrations/<NNN_name>.sql <staging env file>
   ```
   Every migration is safe to run twice.
3. **Check the schema** on staging:
   ```
   python scripts/check_schema.py <staging env file>
   ```
   It must print `SCHEMA OK` (exit code 0). `SCHEMA MISMATCH: missing <table.column>, run
   the pending migrations: <files>` means step 2 isn't finished: run those files and check
   again.
4. **Deploy** the agent.

The agent runs the same check when it starts. With `TEST_MODE` off, a mismatch means it
refuses to take calls (the worker exits with code 1, and a call that still reaches a job
is refused before anything is said). With `TEST_MODE` on, calls use in-memory data and the
mismatch is logged at ERROR on every call. See `docs/DECISIONS.md`.
