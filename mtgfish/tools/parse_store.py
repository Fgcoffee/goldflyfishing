"""Build and check the stored parse (``mtgfish.parser.store``).

    python -m mtgfish.tools.parse_store status          # fresh, stale (why) or missing
    python -m mtgfish.tools.parse_store build           # parse every card, write the store
    python -m mtgfish.tools.parse_store ensure          # build only if stale or missing
    python -m mtgfish.tools.parse_store ensure --background
    python -m mtgfish.tools.parse_store verify --sample 500
    python -m mtgfish.tools.parse_store install-hooks   # rebuild after commits, merges, checkouts

``ensure`` is what the git hooks and the Scryfall fetch run. It takes a lock,
so two hooks firing together build once. ``verify`` re-parses a random sample
live and compares it with what the store serves - the store is a cache, and
this is the check that it is one.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent


def _db():
    from ..data.db import CardDatabase

    db = CardDatabase()
    db.registry()
    return db


def _lock_path() -> Path:
    from ..parser.store import store_path

    return store_path().with_suffix(".lock")


def _take_lock() -> bool:
    """A lock file holding the builder's pid; a dead holder's lock is taken over."""
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            pid = int(path.read_text().strip() or "0")
            os.kill(pid, 0)
            return False  # a live build is running
        except (ValueError, ProcessLookupError, PermissionError):
            path.unlink(missing_ok=True)
            return _take_lock()
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    return True


def cmd_status(_args) -> int:
    from ..parser.store import status

    info = status(_db())
    print(f"{info['state']}: {info['path']}")
    for key in ("built_at", "faces"):
        if info.get(key):
            print(f"  {key}: {info[key]}")
    for reason in info.get("reasons", ()):
        print(f"  stale: {reason}")
    return 0 if info["state"] == "fresh" else 1


def _progress(done: int, total: int) -> None:
    if done == total or done % 20 == 0:
        print(f"  {done}/{total} chunks", flush=True)


def cmd_build(args) -> int:
    from ..parser.store import build

    if not _take_lock():
        print("another build is running")
        return 0
    try:
        os.environ["MTGFISH_PARSE_STORE"] = "off"
        meta = build(_db(), jobs=args.jobs, progress=_progress)
    finally:
        _lock_path().unlink(missing_ok=True)
    print(f"built {meta['faces']} faces of {meta['cards']} cards in {meta['seconds']}s")
    return 0


def cmd_ensure(args) -> int:
    from ..parser.store import status

    if status(_db())["state"] == "fresh":
        return 0
    if args.background:
        log = _lock_path().with_suffix(".log")
        with open(log, "a") as out:
            out.write(f"\n--- {time.strftime('%Y-%m-%dT%H:%M:%S')} rebuild\n")
            out.flush()
            subprocess.Popen(
                [sys.executable, "-m", "mtgfish.tools.parse_store", "build"],
                cwd=_REPO,
                stdout=out,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        print(f"parse store stale; rebuilding in the background (log: {log})")
        return 0
    return cmd_build(args)


def cmd_verify(args) -> int:
    """Re-parse a random sample live and compare with the stored answer."""
    import random

    from ..parser.compile import parse_face
    from ..parser.store import ParseStore

    db = _db()
    store = ParseStore.open(db)
    if store is None or not store.fresh:
        print("no fresh store to verify")
        return 1
    cards = list(db.iter_cards())
    sample = random.SystemRandom().sample(cards, min(args.sample, len(cards)))
    checked = mismatched = missing = 0
    for card in sample:
        for face_index in range(max(1, len(card.faces))):
            stored = store.get(card, face_index)
            if stored is None:
                missing += 1
                continue
            checked += 1
            live = parse_face(card, face_index)
            if repr(tuple(live.abilities)) != repr(stored[0]):
                mismatched += 1
                print(f"MISMATCH: {card.name} face {face_index}")
    print(f"{checked} faces checked, {mismatched} mismatched, {missing} not in the store")
    return 1 if mismatched else 0


_HOOKS = ("post-commit", "post-merge", "post-checkout", "post-rewrite")


def cmd_install_hooks(_args) -> int:
    """Point git at the repository's ``.githooks``, which run ``ensure --background``."""
    subprocess.run(["git", "config", "core.hooksPath", ".githooks"], cwd=_REPO, check=True)
    for name in _HOOKS:
        hook = _REPO / ".githooks" / name
        if hook.exists():
            hook.chmod(0o755)
    print("git will rebuild the parse store after commits, merges, checkouts and rebases")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status").set_defaults(func=cmd_status)
    build = sub.add_parser("build")
    build.add_argument("--jobs", type=int, default=None)
    build.set_defaults(func=cmd_build)
    ensure = sub.add_parser("ensure")
    ensure.add_argument("--jobs", type=int, default=None)
    ensure.add_argument("--background", action="store_true")
    ensure.set_defaults(func=cmd_ensure)
    verify = sub.add_parser("verify")
    verify.add_argument("--sample", type=int, default=500)
    verify.set_defaults(func=cmd_verify)
    sub.add_parser("install-hooks").set_defaults(func=cmd_install_hooks)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
