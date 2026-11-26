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
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS mcp_servers JSON",
]


def migrate():
    from sqlalchemy import text
    with engine.begin() as conn:
        for sql in MIGRATIONS:
            conn.execute(text(sql))
