"""Does the database have every table and column the code needs?

What the code needs is read from db/schema.sql (its CREATE TABLE and ADD COLUMN lines),
so the two can't drift apart. Each column a numbered migration adds is mapped to that
file, so a mismatch names the migrations to run:

    SCHEMA MISMATCH: missing call_attempts.phone_hash, run the pending migrations:
    db/migrations/003_number_check.sql

Used by the agent at startup (agent/main.py) and by scripts/check_schema.py.
"""
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
SCHEMA = ROOT / "db" / "schema.sql"
MIGRATIONS = ROOT / "db" / "migrations"

_CREATE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\((.*?)\n\);", re.I | re.S)
_ADD = re.compile(r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", re.I)
_NOT_A_COLUMN = {"unique", "primary", "check", "constraint", "foreign", "exclude"}


def _no_comments(sql):
    return re.sub(r"--[^\n]*", "", sql)


def columns_in(sql):
    """{(table, column)} that the SQL creates or adds."""
    sql = _no_comments(sql)
    found = set()
    for table, body in _CREATE.findall(sql):
        for line in body.split("\n"):
            word = re.match(r"\s*(\w+)", line)
            if word and word.group(1).lower() not in _NOT_A_COLUMN:
                found.add((table.lower(), word.group(1).lower()))
    found |= {(t.lower(), c.lower()) for t, c in _ADD.findall(sql)}
    return found


def needed():
    """Every (table, column) the code needs: what db/schema.sql creates."""
    return columns_in(SCHEMA.read_text(encoding="utf-8"))


def migration_for():
    """{(table, column): "db/migrations/NNN_name.sql"} for columns a migration adds."""
    out = {}
    for path in sorted(MIGRATIONS.glob("*.sql")):
        for col in columns_in(path.read_text(encoding="utf-8")):
            out.setdefault(col, f"db/migrations/{path.name}")
    return out


def compare(have, need=None):
    """have: {(table, column)} in the database. Returns {"missing": ["table.column", ...],
    "migrations": [files to run, oldest first]}; both empty when nothing is missing."""
    need = needed() if need is None else need
    missing = sorted(need - {(t.lower(), c.lower()) for t, c in have})
    where = migration_for()
    files = sorted({where.get(col, "db/schema.sql") for col in missing},
                   key=lambda f: (f == "db/schema.sql", f))
    return {"missing": [f"{t}.{c}" for t, c in missing], "migrations": files}


def message(result):
    """The line that is logged and printed: "SCHEMA OK" or "SCHEMA MISMATCH: ..."."""
    if not result["missing"]:
        return f"SCHEMA OK: all {len(needed())} columns the code needs are there"
    return ("SCHEMA MISMATCH: missing " + ", ".join(result["missing"])
            + ", run the pending migrations: " + ", ".join(result["migrations"]))


async def database_columns(conn):
    rows = await conn.fetch(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema()")
    return {(r["table_name"], r["column_name"]) for r in rows}


async def check(conn):
    """Compare the database on this open asyncpg connection with what the code needs."""
    return compare(await database_columns(conn))
