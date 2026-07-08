"""Shared fixtures.

Integration tests need a Postgres server (the models use JSONB, UUID, and a
partial unique index — SQLite can't stand in). Locally that's the compose
postgres on :5433; in CI it's a service container. When no server is
reachable, DB-backed tests skip rather than fail.

TEST_DATABASE_URL points at the server; the fixture creates (and reuses) a
dedicated `oncall_test` database so tests never touch demo data.
"""

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.db.models import Base

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://oncall:oncall@localhost:5433/oncall_test",
)


@pytest.fixture(scope="session")
def pg_engine():
    url = make_url(TEST_DATABASE_URL)
    admin_url = url.set(database="postgres")
    try:
        admin_engine = create_engine(
            admin_url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 2}
        )
        with admin_engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": url.database},
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{url.database}"'))
        admin_engine.dispose()
    except OperationalError:
        pytest.skip(
            "postgres not reachable — start it with `docker compose up -d postgres` "
            f"or set TEST_DATABASE_URL (tried {admin_url.render_as_string(hide_password=True)})"
        )

    engine = create_engine(url)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(pg_engine):
    """A clean session per test: child tables first, then incidents."""
    factory = sessionmaker(bind=pg_engine, expire_on_commit=False)
    session = factory()
    for table in ("timeline_events", "llm_analyses", "incidents"):
        session.execute(text(f"DELETE FROM {table}"))
    session.commit()
    yield session
    session.rollback()
    session.close()
