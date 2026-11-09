"""The web server's configuration, all of it from the environment.

Nothing here is a secret yet; secrets (OAuth, session signing, the database)
arrive with accounts and are read the same way - never from a committed file.
A local ``.env`` (git-ignored) is loaded first if present, so a developer can
keep them out of their shell profile.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def load_dotenv(path: Path | None = None) -> None:
    """Read ``KEY=value`` lines from a ``.env`` into the environment.

    Deliberately small: no interpolation, no export keyword, and a variable
    already set in the environment always wins - so production, which sets
    real variables, can never be overridden by a stray file.
    """
    path = path or Path.cwd() / ".env"
    try:
        lines = path.read_text(encoding="utf8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in ("", "0", "false", "no", "off")


@dataclass
class Settings:
    #: Concurrent browser sessions, each with its own bridge (~30 MB each).
    max_sessions: int = field(default_factory=lambda: _env_int("MTGFISH_MAX_SESSIONS", 16))
    #: Seconds a session may sit unused before it is closed.
    session_ttl: int = field(default_factory=lambda: _env_int("MTGFISH_SESSION_TTL", 4 * 3600))
    #: Simulations running at once, across everyone. A run already uses a
    #: process per core, so two at once are each half as fast, not twice as
    #: useful. Further runs wait in the queue.
    max_runs: int = field(default_factory=lambda: _env_int("MTGFISH_MAX_RUNS", 1))
    #: Runs one browser (later: one account) may have running at once.
    max_runs_per_user: int = field(
        default_factory=lambda: _env_int("MTGFISH_MAX_RUNS_PER_USER", 1)
    )
    #: Runs one browser may have waiting in the queue. Past this a start is
    #: refused rather than queued, so nobody can fill the queue for everyone.
    max_queued_per_user: int = field(
        default_factory=lambda: _env_int("MTGFISH_MAX_QUEUED_PER_USER", 2)
    )
    #: Seconds a page may be gone before its run is stopped. Long enough that
    #: reloading - including into a new version - keeps the run; short enough
    #: that closing the tab frees the machine. Negative never stops it.
    abandon_grace: int = field(default_factory=lambda: _env_int("MTGFISH_ABANDON_GRACE", 30))
    #: Seconds a single slot call may take before the page is told it failed.
    call_timeout: int = field(default_factory=lambda: _env_int("MTGFISH_CALL_TIMEOUT", 180))
    #: Slots the browser may not call. ``read_file`` reads any path on the
    #: server's disk - fine in a desktop window, not on the internet.
    deny: frozenset[str] = field(
        default_factory=lambda: frozenset(
            name.strip()
            for name in os.environ.get("MTGFISH_WEB_DENY", "read_file").split(",")
            if name.strip()
        )
    )
    secure_cookies: bool = field(default_factory=lambda: _env_bool("MTGFISH_SECURE_COOKIES"))
    #: The address people reach the site at, e.g. https://goldfisher.example.
    #: OAuth redirect URIs are built from it.
    public_base_url: str = field(
        default_factory=lambda: os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    )
