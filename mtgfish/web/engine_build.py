"""The fingerprint of the TypeScript engine this server runs.

A browser run records the engine build it was played with (the webapp's
``vite.config.ts`` bakes the same fingerprint into the bundle). The server
only accepts runs from its own build, so it always has the right engine to
replay a sample of them (the spot check).

The fingerprint is a sha256 over every ``.ts`` file under ``engine/src``
except tests: each file's relative POSIX path, a NUL, its text with CRLF
turned into LF, and a NUL, in path order. The first 16 hex digits. This must
stay byte-for-byte the same as ``engineBuild()`` in ``webapp/vite.config.ts``.

``MTGFISH_ENGINE_BUILD`` overrides it, for a deploy that ships a built
bundle without the engine source. None means unknown, and the server then
doesn't gate uploads by build.
"""

from __future__ import annotations

import functools
import hashlib
import os
from pathlib import Path

ENGINE_SRC = Path(__file__).resolve().parent.parent.parent / "engine" / "src"


def fingerprint(root: Path) -> str | None:
    if not root.is_dir():
        return None
    files = sorted(
        (path.relative_to(root).as_posix(), path)
        for path in root.rglob("*.ts")
        if not path.name.endswith(".test.ts")
    )
    digest = hashlib.sha256()
    for name, path in files:
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(path.read_text(encoding="utf-8").replace("\r\n", "\n").encode())
        digest.update(b"\0")
    return digest.hexdigest()[:16]


@functools.cache
def current() -> str | None:
    """This server's engine build, computed once per process."""
    return os.environ.get("MTGFISH_ENGINE_BUILD") or fingerprint(ENGINE_SRC)
