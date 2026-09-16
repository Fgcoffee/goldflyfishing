"""An HTTP server for the web UI.

Standard library only. The bridge is synchronous, owns a sqlite connection
that must stay on the thread that opened it, and runs simulations on threads
of its own - a threaded server fits that shape directly, where an async
framework would spend its effort hopping back onto one thread anyway.

Routes::

    GET  /                  the page (and every other file in ui/web)
    GET  /api/methods       which slots and signals this server has
    GET  /api/version       a hash that changes when the served app changes
    POST /api/call/<slot>   {"args": [...]} -> the slot's own JSON reply
    GET  /api/events        server-sent events: {"signal", "args"}
    GET  /healthz           liveness, session count, whether a run is going

Each browser gets its own ``Bridge`` - its own sandbox, last run and Archidekt
sign-in - keyed by a cookie. Every call to a bridge happens on that session's
one dedicated thread, which is what the desktop app's UI thread was.

Behind a TLS-terminating proxy in production: the Archidekt sign-in carries a
password, and it must never cross the network in the clear.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import mimetypes
import os
import queue
import secrets
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

# Before anything imports the bridge: a server has no use for Qt, and must
# behave the same whether or not PySide6 happens to be installed.
os.environ.setdefault("MTGFISH_NO_QT", "1")

log = logging.getLogger("mtgfish.web")

PACKAGE = Path(__file__).resolve().parent.parent
WEB = PACKAGE / "ui" / "web"

COOKIE = "mtgfish_sid"

#: Signals that carry only progress. Frequent, disposable, and never replayed
#: to a reconnecting page - the next one is a second away.
TRANSIENT_SIGNALS = {"progressed"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    #: Concurrent browser sessions, each with its own bridge.
    max_sessions: int = field(default_factory=lambda: _env_int("MTGFISH_MAX_SESSIONS", 16))
    #: Seconds a session may sit unused before it is closed.
    session_ttl: int = field(default_factory=lambda: _env_int("MTGFISH_SESSION_TTL", 4 * 3600))
    #: Simulations running at once, across all sessions. A run already uses a
    #: process per core, so two at once are each half as fast, not twice as
    #: useful.
    max_runs: int = field(default_factory=lambda: _env_int("MTGFISH_MAX_RUNS", 1))
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
    secure_cookies: bool = field(
        default_factory=lambda: os.environ.get("MTGFISH_SECURE_COOKIES", "") not in ("", "0")
    )


# ---------------------------------------------------------------------------
# What the bridge offers
# ---------------------------------------------------------------------------


class Api:
    """The bridge's slots and signals, discovered rather than listed.

    Discovered once per process. Code changes arrive by restarting the process
    (``--reload`` does it), so there is nothing to re-discover while running.
    """

    def __init__(self, settings: Settings) -> None:
        from ..ui.bridge import Bridge
        from ..ui.qtcompat import is_slot, signal_names

        self.bridge_class = Bridge
        self.signals = signal_names(Bridge)
        self.methods = sorted(
            name
            for name in dir(Bridge)
            if not name.startswith("_")
            and name not in settings.deny
            and name not in self.signals
            and callable(getattr(Bridge, name, None))
            and is_slot(Bridge, name)
        )


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class ServerFull(RuntimeError):
    pass


class Session:
    """One browser's bridge, and the thread every call to it runs on."""

    def __init__(self, sid: str, api: Api, manager: SessionManager) -> None:
        self.sid = sid
        self.api = api
        self.manager = manager
        self.created = self.last_seen = time.time()
        self.running = False
        self._subscribers: list[queue.Queue] = []
        self._backlog: deque[tuple[int, str]] = deque(maxlen=50)
        self._event_id = 0
        self._lock = threading.Lock()
        self._thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"bridge-{sid[:6]}")
        self.bridge = self._thread.submit(self._build).result()

    def _build(self):
        from ..ui.qtcompat import HAS_QT

        bridge = self.api.bridge_class()
        for name in self.api.signals:
            listener = self._listener(name)
            signal = getattr(bridge, name)
            if HAS_QT:  # pragma: no cover - the server normally runs without Qt
                from PySide6.QtCore import Qt

                # Direct: there is no Qt event loop here to queue onto.
                signal.connect(listener, Qt.ConnectionType.DirectConnection)
            else:
                signal.connect(listener)
        return bridge

    def _listener(self, name: str):
        def forward(*args) -> None:
            self.publish(name, list(args))

        return forward

    # -- calls --------------------------------------------------------------

    def call(self, method: str, args: list, timeout: float) -> str:
        self.last_seen = time.time()
        future = self._thread.submit(getattr(self.bridge, method), *args)
        return future.result(timeout=timeout)

    # -- events -------------------------------------------------------------

    def publish(self, signal: str, args: list) -> None:
        if signal.endswith("_finished"):
            self.running = False
        with self._lock:
            self._event_id += 1
            event_id = self._event_id
            payload = json.dumps({"signal": signal, "args": args}, default=str)
            if signal not in TRANSIENT_SIGNALS:
                self._backlog.append((event_id, payload))
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait((event_id, payload))
            except queue.Full:
                pass  # A stalled page misses progress ticks, not results.

    def subscribe(self, last_event_id: int | None) -> queue.Queue:
        """A queue of events, primed with any this page missed while away.

        A run finishing while the page's connection was dropping would
        otherwise be a result nobody ever sees.
        """
        subscriber: queue.Queue = queue.Queue(maxsize=2000)
        with self._lock:
            if last_event_id is not None:
                for event_id, payload in self._backlog:
                    if event_id > last_event_id:
                        subscriber.put_nowait((event_id, payload))
            self._subscribers.append(subscriber)
        self.last_seen = time.time()
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue) -> None:
        with self._lock:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)
            abandoned = not self._subscribers and self.running
        self.last_seen = time.time()
        if abandoned:
            self._schedule_abandon()

    @property
    def watched(self) -> bool:
        return bool(self._subscribers)

    # -- stopping -----------------------------------------------------------

    def _schedule_abandon(self) -> None:
        """Stop the run if its page does not come back.

        The event stream is the only sign a page is still open; it drops when
        the tab closes and also, briefly, when the page reloads. So the run
        gets a grace period rather than being stopped the moment it drops.
        """
        grace = self.manager.settings.abandon_grace
        if grace < 0:
            return
        timer = threading.Timer(grace, self._abandon_if_still_gone)
        timer.daemon = True
        timer.start()

    def _abandon_if_still_gone(self) -> None:
        if self.watched or not self.running:
            return
        log.info("session %s: page closed during a run; stopping the run", self.sid[:6])
        self.cancel()

    def cancel(self) -> None:
        """Ask the bridge to stop its run, if it has one and knows how."""
        stop = getattr(self.bridge, "cancel_run", None)
        if stop is None:
            return
        try:
            self._thread.submit(stop).result(timeout=10)
        except Exception:  # noqa: BLE001 - stopping is best effort
            log.warning("session %s: could not stop its run", self.sid[:6], exc_info=True)

    def close(self) -> None:
        # Stop the run first and wait for its processes: closing the database
        # under a running simulation, or exiting with its workers still
        # playing, is the ungraceful shutdown this is here to avoid.
        shutdown = getattr(self.bridge, "shutdown", None)
        if shutdown is not None:
            try:
                shutdown()
            except Exception:  # noqa: BLE001
                pass
        else:
            self.cancel()

        def shut() -> None:
            try:
                self.bridge.db.close()
            except Exception:  # noqa: BLE001 - closing is best effort
                pass

        try:
            self._thread.submit(shut).result(timeout=10)
        except Exception:  # noqa: BLE001
            pass
        self._thread.shutdown(wait=False)


class SessionManager:
    def __init__(self, api: Api, settings: Settings) -> None:
        self.api = api
        self.settings = settings
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        self._reaper = threading.Thread(target=self._reap_forever, daemon=True, name="reaper")
        self._reaper.start()

    def get(self, sid: str) -> Session:
        with self._lock:
            session = self._sessions.get(sid)
            if session is not None:
                session.last_seen = time.time()
                return session
            if len(self._sessions) >= self.settings.max_sessions:
                self._evict_one()
            session = Session(sid, self.api, self)
            self._sessions[sid] = session
            log.info("session %s opened (%d open)", sid[:6], len(self._sessions))
            return session

    def _evict_one(self) -> None:
        idle = [s for s in self._sessions.values() if not s.running and not s.watched]
        if not idle:
            raise ServerFull("every session is in use")
        oldest = min(idle, key=lambda s: s.last_seen)
        del self._sessions[oldest.sid]
        oldest.close()

    def _reap_forever(self) -> None:
        while True:
            time.sleep(60)
            cutoff = time.time() - self.settings.session_ttl
            with self._lock:
                stale = [
                    s for s in self._sessions.values()
                    if s.last_seen < cutoff and not s.running and not s.watched
                ]
                for session in stale:
                    del self._sessions[session.sid]
            for session in stale:
                session.close()

    def close_all(self) -> None:
        """Stop every run and close every session, for a server shutting down."""
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        # Every run is told to stop before any is waited on, so a server with
        # several running does not stop them one at a time.
        for session in sessions:
            if session.running:
                session.cancel()
        for session in sessions:
            session.close()

    @property
    def count(self) -> int:
        return len(self._sessions)

    @property
    def runs(self) -> int:
        return sum(1 for s in list(self._sessions.values()) if s.running)


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------


def _fingerprint(paths) -> str:
    digest = hashlib.sha1()
    for path in sorted(paths):
        try:
            stat = path.stat()
        except OSError:
            continue
        digest.update(f"{path.relative_to(PACKAGE)}|{stat.st_mtime_ns}|{stat.st_size}\n".encode())
    return digest.hexdigest()[:12]


def code_files() -> list[Path]:
    return [p for p in PACKAGE.rglob("*.py") if "__pycache__" not in p.parts]


def web_files() -> list[Path]:
    return [p for p in WEB.rglob("*") if p.is_file()]


class Versions:
    """What a page needs to know to tell that it is out of date.

    The code part is read at start-up and never again: Python that changed on
    disk is not running until the process restarts, so reporting it early
    would tell the page to reload into the same old code. The web files are
    served from disk on every request, so their part is live.
    """

    def __init__(self) -> None:
        self.code = _fingerprint(code_files())
        self._web = ""
        self._checked = 0.0
        self._lock = threading.Lock()

    @property
    def current(self) -> str:
        with self._lock:
            if time.time() - self._checked > 2:
                self._web = _fingerprint(web_files())
                self._checked = time.time()
            return f"{self.code}-{self._web}"


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

_COMPRESSIBLE = ("text/", "application/javascript", "application/json", "image/svg+xml")

_CSP = (
    "default-src 'self'; "
    "img-src 'self' data: https://cards.scryfall.io https://api.scryfall.com "
    "https://svgs.scryfall.io; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


class App:
    """Everything a request handler needs, built once."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.api = Api(self.settings)
        self.sessions = SessionManager(self.api, self.settings)
        self.versions = Versions()
        self._gzip_cache: dict[tuple[str, str], bytes] = {}
        self.started = time.time()

    def stop(self, server) -> None:
        """Shut down gracefully: runs stopped, sessions closed, then the server."""
        self.sessions.close_all()
        server.shutdown()


class Handler(BaseHTTPRequestHandler):
    server_version = "mtgfish"
    protocol_version = "HTTP/1.1"
    app: App  # set by make_server

    # -- plumbing -----------------------------------------------------------

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        log.debug("%s - %s", self.address_string(), format % args)

    def _sid(self) -> tuple[str, bool]:
        """This browser's session id, and whether it was just issued."""
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get(COOKIE)
        if morsel and 16 <= len(morsel.value) <= 64 and morsel.value.replace("-", "").replace(
            "_", ""
        ).isalnum():
            return morsel.value, False
        return secrets.token_urlsafe(24), True

    def _secure(self) -> bool:
        return self.app.settings.secure_cookies or (
            self.headers.get("X-Forwarded-Proto", "").lower() == "https"
        )

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str,
        *,
        headers: dict | None = None,
        sid: tuple[str, bool] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        if sid and sid[1]:
            self._set_cookie(sid[0])
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _set_cookie(self, sid: str) -> None:
        parts = [f"{COOKIE}={sid}", "Path=/", "HttpOnly", "SameSite=Lax", "Max-Age=2592000"]
        if self._secure():
            parts.append("Secure")
        self.send_header("Set-Cookie", "; ".join(parts))

    def _json(self, status: int, payload, **kwargs) -> None:
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self._send(status, body, "application/json; charset=utf-8",
                   headers={"Cache-Control": "no-store"}, **kwargs)

    # -- routing ------------------------------------------------------------

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib naming
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        try:
            if path == "/healthz":
                return self._json(200, {
                    "ok": True,
                    "sessions": self.app.sessions.count,
                    "busy": self.app.sessions.runs > 0,
                    "version": self.app.versions.current,
                    "uptime": round(time.time() - self.app.started),
                })
            if path == "/api/version":
                return self._json(200, {"version": self.app.versions.current})
            if path == "/api/methods":
                return self._json(200, {
                    "methods": self.app.api.methods,
                    "signals": self.app.api.signals,
                    "version": self.app.versions.current,
                })
            if path == "/api/events":
                return self._events()
            if path.startswith("/api/"):
                return self._json(404, {"error": f"no such endpoint: {path}"})
            return self._static(path)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/api/_shutdown":
            return self._shutdown()
        if not path.startswith("/api/call/"):
            return self._json(404, {"error": f"no such endpoint: {path}"})
        method = path[len("/api/call/"):]
        try:
            self._call(method)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _shutdown(self) -> None:
        """The ``--reload`` supervisor asking for a graceful restart.

        On Windows the supervisor cannot send the server a Ctrl+C of its own,
        and killing the process outright skips stopping the runs. Only the
        supervisor knows the token, and only from this machine; to anyone
        else the endpoint does not exist.
        """
        token = os.environ.get("MTGFISH_SUPERVISOR_TOKEN", "")
        offered = self.headers.get("X-Supervisor-Token", "")
        if (
            not token
            or self.client_address[0] not in ("127.0.0.1", "::1")
            or not secrets.compare_digest(offered, token)
        ):
            return self._json(404, {"error": "no such endpoint: /api/_shutdown"})
        self._json(202, {"stopping": True})
        threading.Thread(target=self.app.stop, args=(self.server,), daemon=True).start()

    # -- slots --------------------------------------------------------------

    def _call(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 5_000_000:
            return self._json(413, {"error": "request too large"})
        raw = self.rfile.read(length) if length else b""

        # A JSON content type cannot be sent cross-site without a CORS
        # preflight, which this server never approves - so a page on another
        # site cannot drive someone's session with their cookie.
        if "application/json" not in self.headers.get("Content-Type", ""):
            return self._json(415, {"error": "send JSON"})
        if method not in self.app.api.methods:
            return self._json(404, {"error": f"the server has no method {method!r}"})
        try:
            body = json.loads(raw or b"{}")
            args = body.get("args", []) if isinstance(body, dict) else []
            if not isinstance(args, list):
                raise ValueError("args must be a list")
        except ValueError as exc:
            return self._json(400, {"error": f"bad request: {exc}"})

        sid = self._sid()
        try:
            session = self.app.sessions.get(sid[0])
        except ServerFull:
            return self._json(503, {"error": "the server is full right now - try again shortly"},
                              sid=sid)

        starts_run = method.startswith("start_")
        if starts_run and not session.running and self.app.sessions.runs >= self.app.settings.max_runs:
            return self._json(200, {
                "error": "another simulation is running on this server - try again when it finishes"
            }, sid=sid)

        # JSON has one number type; a slot declared ``int`` must not get 5.0.
        args = [int(a) if isinstance(a, float) and a.is_integer() else a for a in args]
        try:
            reply = session.call(method, args, self.app.settings.call_timeout)
        except TypeError as exc:
            return self._json(400, {"error": f"{method}: {exc}"}, sid=sid)
        except TimeoutError:
            return self._json(504, {"error": f"{method} took too long"}, sid=sid)
        except Exception as exc:  # noqa: BLE001 - the page needs to hear about it
            log.exception("slot %s failed", method)
            return self._json(500, {"error": f"{type(exc).__name__}: {exc}"}, sid=sid)

        if starts_run:
            try:
                if json.loads(reply).get("started"):
                    session.running = True
            except (ValueError, AttributeError):
                pass
        body = reply.encode() if isinstance(reply, str) else json.dumps(reply).encode()
        self._json(200, body, sid=sid)

    # -- events -------------------------------------------------------------

    def _events(self) -> None:
        sid = self._sid()
        try:
            session = self.app.sessions.get(sid[0])
        except ServerFull:
            return self._json(503, {"error": "the server is full right now"}, sid=sid)

        last = self.headers.get("Last-Event-ID")
        subscriber = session.subscribe(int(last) if last and last.isdigit() else None)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")  # nginx: do not buffer the stream
        self.send_header("Connection", "close")
        if sid[1]:
            self._set_cookie(sid[0])
        self.end_headers()
        self.close_connection = True

        try:
            hello = json.dumps({"version": self.app.versions.current})
            self.wfile.write(f"event: hello\ndata: {hello}\nretry: 3000\n\n".encode())
            self.wfile.flush()
            while True:
                try:
                    # Short: a closed tab is only noticed when a write fails,
                    # and that starts the clock on stopping its run.
                    event_id, payload = subscriber.get(timeout=5)
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    continue
                self.wfile.write(f"id: {event_id}\ndata: {payload}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            session.unsubscribe(subscriber)

    # -- files --------------------------------------------------------------

    def _static(self, path: str) -> None:
        relative = path.lstrip("/") or "index.html"
        target = (WEB / relative).resolve()
        if not target.is_relative_to(WEB) or not target.is_file():
            return self._json(404, {"error": "not found"})

        stat = target.stat()
        etag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type == "application/javascript":
            content_type += "; charset=utf-8"
        headers = {
            # Always revalidate. The folder is edited in place, and a browser
            # holding yesterday's app.js against today's bridge is exactly the
            # kind of breakage that looks like a bug in both.
            "Cache-Control": "no-cache",
            "ETag": etag,
            "Vary": "Accept-Encoding",
        }
        if relative == "index.html":
            headers["Content-Security-Policy"] = _CSP
            headers["X-Frame-Options"] = "DENY"

        sid = self._sid() if relative == "index.html" else None
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", "0")
            if sid and sid[1]:
                self._set_cookie(sid[0])
            self.end_headers()
            return

        body = target.read_bytes()
        if (
            content_type.startswith(_COMPRESSIBLE)
            and len(body) > 1024
            and "gzip" in self.headers.get("Accept-Encoding", "")
        ):
            key = (str(target), etag)
            compressed = self.app._gzip_cache.get(key)
            if compressed is None:
                compressed = gzip.compress(body, compresslevel=6)
                self.app._gzip_cache = {
                    k: v for k, v in self.app._gzip_cache.items() if k[0] != str(target)
                }
                self.app._gzip_cache[key] = compressed
            body = compressed
            headers["Content-Encoding"] = "gzip"
        self._send(200, body, content_type, headers=headers, sid=sid)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address) -> None:
        """Stay quiet when a browser simply hangs up.

        Closing a tab, reloading, or the page reconnecting its event stream
        drops a kept-alive socket, and the standard library prints a full
        traceback for each one - noise that buries the errors that matter.
        """
        import sys

        if isinstance(sys.exc_info()[1], (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


def make_server(host: str, port: int, app: App | None = None) -> Server:
    app = app or App()
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = Server((host, port), handler)
    server.app = app  # type: ignore[attr-defined]
    return server
