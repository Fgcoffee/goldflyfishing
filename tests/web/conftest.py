"""Web tests never touch a real accounts database."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _private_web_database(tmp_path_factory):
    path = tmp_path_factory.mktemp("webdb") / "web.sqlite"
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite:///{path.as_posix()}"
    yield
    if previous is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = previous
