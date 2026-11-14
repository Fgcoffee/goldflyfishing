"""Web tests never touch a real accounts database."""

from __future__ import annotations

import os

import pytest

#: pyproject turns every DeprecationWarning into an error. Some anyio
#: releases deprecate ``anyio.abc.BlockingPortal``, which Starlette's
#: TestClient uses internally; that is a dependency's problem, not ours, and
#: it errored every TestClient test in a fresh environment. Ignore exactly
#: that warning, here only, so any other deprecation still fails.
BLOCKING_PORTAL_WARNING = "ignore:.*BlockingPortal.*:DeprecationWarning"


def pytest_configure(config):
    config.addinivalue_line("filterwarnings", BLOCKING_PORTAL_WARNING)


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
