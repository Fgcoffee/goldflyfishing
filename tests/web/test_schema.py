"""The migrations and the models describe the same database."""

from __future__ import annotations


def test_migrations_match_the_models(tmp_path):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from mtgfish.web import models  # noqa: F401
    from mtgfish.web.db import Base, Database

    database = Database(f"sqlite:///{(tmp_path / 'schema.sqlite').as_posix()}")
    with database.engine.connect() as connection:
        context = MigrationContext.configure(connection)
        differences = compare_metadata(context, Base.metadata)
    assert differences == [], differences


def test_migrating_twice_is_harmless(tmp_path):
    from mtgfish.web.db import Database, migrate

    url = f"sqlite:///{(tmp_path / 'twice.sqlite').as_posix()}"
    database = Database(url)
    migrate(database.engine)
