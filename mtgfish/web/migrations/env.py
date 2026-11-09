"""Alembic environment for the web app's database.

Run at start-up by ``db.migrate`` (which hands over its own connection), or
from the command line with the repository's ``alembic.ini``::

    DATABASE_URL=postgresql+psycopg://... alembic upgrade head
"""

from __future__ import annotations

from alembic import context

from mtgfish.web import models  # noqa: F401 - registers the tables
from mtgfish.web.db import Base, database_url, make_engine

target_metadata = Base.metadata


def run() -> None:
    connection = context.config.attributes.get("connection")
    if connection is not None:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    engine = make_engine(database_url())
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


run()
