"""The web app's own database: accounts, sessions and (later) saved decks.

``DATABASE_URL`` picks it: Postgres in production
(``postgresql+psycopg://user:pass@host/db``), and by default a SQLite file
next to the card database for local use and tests. The schema is owned by
Alembic migrations in ``mtgfish/web/migrations``, applied at start-up, so a
deploy never runs against a database older than its code.

This is not the card database. That one is rebuilt from Scryfall and is
read-only; this one holds people's data and is never rebuilt.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

MIGRATIONS = Path(__file__).resolve().parent / "migrations"


class Base(DeclarativeBase):
    pass


def default_url() -> str:
    from ..paths import data_root

    return f"sqlite:///{(data_root() / 'web.sqlite').as_posix()}"


def database_url() -> str:
    return os.environ.get("DATABASE_URL") or default_url()


def make_engine(url: str | None = None) -> Engine:
    url = url or database_url()
    kwargs: dict = {"future": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        # One connection per thread is SQLite's rule; FastAPI's thread pool
        # hands requests to many threads.
        kwargs["connect_args"] = {"check_same_thread": False}
        if url in ("sqlite://", "sqlite:///:memory:"):
            from sqlalchemy.pool import StaticPool

            kwargs["poolclass"] = StaticPool
        else:
            path = url.split("///", 1)[-1]
            Path(path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(connection, _record) -> None:
            cursor = connection.cursor()
            # Foreign keys are off in SQLite unless asked for, and deleting an
            # account relies on them cascading.
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def migrate(engine: Engine) -> None:
    """Bring the schema up to date (``alembic upgrade head``)."""
    from alembic import command
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


class Database:
    """An engine and a session factory, built once per app."""

    def __init__(self, url: str | None = None, *, run_migrations: bool = True) -> None:
        self.engine = make_engine(url)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False, future=True)
        if run_migrations:
            migrate(self.engine)

    def session(self) -> Session:
        return self.sessions()
