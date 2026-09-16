"""``python -m mtgfish.web`` serves the app.

    python -m mtgfish.web                       http://127.0.0.1:8000
    python -m mtgfish.web --host 0.0.0.0 --port 8080
    python -m mtgfish.web --reload              restart when mtgfish/ changes

``--reload`` exists because this folder keeps changing under a running server.
Changed web files (HTML, CSS, JS) need no restart at all - they are served from
disk on every request, and open pages are told to refresh. Changed Python
does: the supervisor notices, waits for any simulation in progress to finish
rather than killing it, and restarts the server. A change that fails to import
leaves the supervisor waiting for the next change instead of exiting, so a
half-saved file does not take the site down for good.
"""

from __future__ import annotations

import argparse
import json
import logging
import multiprocessing
import os
import subprocess
import sys
import time
import urllib.request


def _parse(argv: list[str] | None) -> argparse.Namespace:
    # No ``prog``: argparse takes it from ``sys.argv[0]``, which both entry
    # points set to however the user actually started this - "mtgfish web" or
    # "python -m mtgfish.web". Hardcoding one of them tells half the users to
    # run a command they did not type.
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--host", default=os.environ.get("MTGFISH_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument(
        "--reload",
        action="store_true",
        default=os.environ.get("MTGFISH_RELOAD", "") not in ("", "0"),
        help="restart the server when Python files under mtgfish/ change",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="log every request")
    return parser.parse_args(argv)


def serve(args: argparse.Namespace) -> int:
    os.environ.setdefault("MTGFISH_NO_QT", "1")
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    from ..bootstrap import seed_card_database

    database = seed_card_database(report=print)
    if not database.exists():
        # Name the command that builds it the same way they started this one,
        # rather than telling someone who typed "mtgfish web" to go and run a
        # python -m line they have no reason to expect works.
        fetch = (
            "mtgfish fetch"
            if sys.argv[0].startswith("mtgfish ")
            else "python -m mtgfish.tools.fetch_scryfall"
        )
        print(
            f"No card database at {database}.\n"
            f"Build it with:  {fetch}\n"
            "or point MTGFISH_DATA_DIR at a directory holding cards.sqlite.",
            file=sys.stderr,
        )
        return 2

    from .server import make_server

    server = make_server(args.host, args.port)
    print(f"MTG Goldfisher serving on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        # Runs stopped and their worker processes gone before exiting, rather
        # than the interpreter waiting at exit for games nobody will see.
        server.app.sessions.close_all()
        server.server_close()
    return 0


# ---------------------------------------------------------------------------
# --reload
# ---------------------------------------------------------------------------


def _snapshot() -> dict:
    from .server import code_files

    out = {}
    for path in code_files():
        try:
            stat = path.stat()
        except OSError:
            continue
        out[str(path)] = (stat.st_mtime_ns, stat.st_size)
    return out


def _busy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=3) as response:
            return bool(json.load(response).get("busy"))
    except Exception:  # noqa: BLE001 - an unreachable child is not busy
        return False


def _stop_child(child: subprocess.Popen, port: int, token: str) -> None:
    """Ask the server to shut down gracefully; kill it only if it will not."""
    if child.poll() is not None:
        return
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/_shutdown",
            data=b"",
            method="POST",
            headers={"X-Supervisor-Token": token},
        )
        urllib.request.urlopen(request, timeout=5).close()
    except Exception:  # noqa: BLE001 - fall back to terminating it
        pass
    try:
        child.wait(timeout=30)
    except subprocess.TimeoutExpired:
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def supervise(args: argparse.Namespace, argv: list[str]) -> int:
    import secrets

    child_argv = [a for a in argv if a != "--reload"]
    token = secrets.token_urlsafe(24)
    env = dict(os.environ, MTGFISH_RELOAD="0", MTGFISH_SUPERVISOR_TOKEN=token)
    # Long enough for an ordinary run to finish; short enough that a run stuck
    # in a pathological game cannot hold the fix for it back for hours.
    max_wait = int(os.environ.get("MTGFISH_RELOAD_MAX_WAIT", str(15 * 60)))

    def start() -> subprocess.Popen:
        return subprocess.Popen([sys.executable, "-m", "mtgfish.web", *child_argv], env=env)

    snapshot = _snapshot()
    child = start()
    try:
        while True:
            time.sleep(1.5)
            current = _snapshot()
            if current == snapshot:
                if child.poll() is not None and child.returncode == 0:
                    return 0
                continue

            # Let an editor finish writing a burst of files before acting.
            while True:
                time.sleep(1.0)
                settled = _snapshot()
                if settled == current:
                    break
                current = settled
            snapshot = current

            if child.poll() is None:
                waited = 0
                while _busy(args.port) and waited < max_wait:
                    if waited == 0:
                        print("[reload] code changed; waiting for the running simulation", flush=True)
                    time.sleep(5)
                    waited += 5
                print("[reload] code changed; restarting", flush=True)
                _stop_child(child, args.port, token)
            else:
                print("[reload] code changed; starting again", flush=True)
            child = start()
    except KeyboardInterrupt:
        return 0
    finally:
        # Ctrl+C reaches the server too, and it shuts down by itself; this
        # only steps in if it has not.
        try:
            child.wait(timeout=20)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            _stop_child(child, args.port, token)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = _parse(argv)
    if args.reload:
        return supervise(args, argv)
    return serve(args)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.argv[0] = "python -m mtgfish.web"
    raise SystemExit(main())
