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


def migrate():
    from sqlalchemy import text
    with engine.begin() as conn:
        for sql in MIGRATIONS:
            conn.execute(text(sql))
