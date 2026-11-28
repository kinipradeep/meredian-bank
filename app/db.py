from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings

engine = create_engine(get_settings().database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


MIGRATIONS = [  # columns added after the first release; create_all does not alter existing tables
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS mcp_token VARCHAR(80) NOT NULL DEFAULT ''",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(200) NOT NULL DEFAULT ''",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS policies_version VARCHAR(40) NOT NULL DEFAULT ''",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS policies_accepted_at TIMESTAMPTZ",
    # Groq retired Llama 3.1 8B / 3.3 70B on 2026-08-16: move agents to the gateway's replacement models.
    "UPDATE agents SET model = 'groq-gpt-oss-20b' WHERE model = 'groq-llama-8b'",
    "UPDATE agents SET model = 'groq-gpt-oss-120b' WHERE model = 'groq-llama-70b'",
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS mcp_servers JSON",
    "ALTER TABLE challenges ALTER COLUMN domain TYPE VARCHAR(200)",
    "ALTER TABLE challenges ADD COLUMN IF NOT EXISTS category VARCHAR(60) NOT NULL DEFAULT ''",
    "ALTER TABLE challenges ADD COLUMN IF NOT EXISTS difficulty VARCHAR(20) NOT NULL DEFAULT 'easy'",
    "ALTER TABLE challenges ADD COLUMN IF NOT EXISTS kind VARCHAR(20) NOT NULL DEFAULT 'detector'",
    "ALTER TABLE challenges ADD COLUMN IF NOT EXISTS hints JSON",
    "ALTER TABLE challenges ADD COLUMN IF NOT EXISTS agent_slug VARCHAR(80) NOT NULL DEFAULT ''",
    "ALTER TABLE challenges ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 100",
    "ALTER TABLE challenge_solves ADD COLUMN IF NOT EXISTS points INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE challenge_solves ADD COLUMN IF NOT EXISTS first_blood BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE challenges ADD COLUMN IF NOT EXISTS module VARCHAR(120) NOT NULL DEFAULT ''",
    "ALTER TABLE challenges ADD COLUMN IF NOT EXISTS module_order INTEGER NOT NULL DEFAULT 100",
    "ALTER TABLE challenges ADD COLUMN IF NOT EXISTS objectives JSON",
    "ALTER TABLE challenges ADD COLUMN IF NOT EXISTS steps JSON",
    "ALTER TABLE challenges ADD COLUMN IF NOT EXISTS answer TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE challenges ADD COLUMN IF NOT EXISTS defence TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE challenges ADD COLUMN IF NOT EXISTS hardened_slug VARCHAR(80) NOT NULL DEFAULT ''",
    # solves recorded before CTF scoring existed: award the challenge's points
    "UPDATE challenge_solves s SET points = c.points FROM challenges c WHERE s.challenge_id = c.id AND s.points = 0",
]


def _already_applied(conn, sql: str) -> bool:
    """True if an ALTER TABLE in MIGRATIONS has nothing left to do, so it can be skipped without taking a lock."""
    import re
    from sqlalchemy import text
    m = re.match(r"ALTER TABLE (\w+) ADD COLUMN IF NOT EXISTS (\w+)", sql)
    if m:
        return conn.execute(text("SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c"),
                            {"t": m.group(1), "c": m.group(2)}).first() is not None
    m = re.match(r"ALTER TABLE (\w+) ALTER COLUMN (\w+) TYPE VARCHAR\((\d+)\)", sql)
    if m:
        row = conn.execute(text("SELECT character_maximum_length FROM information_schema.columns "
                                "WHERE table_name = :t AND column_name = :c"), {"t": m.group(1), "c": m.group(2)}).first()
        return row is not None and row[0] == int(m.group(3))
    return False


def migrate():
    """Apply MIGRATIONS. Each runs in its own short transaction and is skipped when already applied: ALTER TABLE
    takes an exclusive lock, and holding locks on every table for the whole startup deadlocked with live requests
    (or the lab matrix) while the app restarted."""
    from sqlalchemy import text
    for sql in MIGRATIONS:
        with engine.begin() as conn:
            if sql.startswith("ALTER TABLE") and _already_applied(conn, sql):
                continue
            conn.execute(text("SET LOCAL lock_timeout = '10s'"))
            conn.execute(text(sql))
