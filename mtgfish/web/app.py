"""The web app: FastAPI over the same bridge the desktop window uses.

Routes (the contract the page in ``ui/web`` is written against)::

    GET  /                    the page (and every other file in ui/web)
    GET  /api/methods         which slots and signals this server has
    GET  /api/version         a hash that changes when the served app changes
    POST /api/call/<slot>     {"args": [...]} -> the slot's own JSON reply
    GET  /api/events          server-sent events: {"signal", "args"}
    GET  /api/jobs            this browser's simulations, queued to finished
    POST /api/jobs/<id>/cancel
    GET  /healthz             liveness, session count, whether a run is going

The bridge is synchronous and every call to a session's bridge happens on
that session's own thread, so the async handlers here only ever *wait* for
it - the event loop is never blocked by a slot or a game. Simulations do not
run in requests at all: a ``start_*`` call becomes a job (see ``jobs``).

Behind a TLS-terminating proxy in production: the Archidekt sign-in carries a
password, and it must never cross the network in the clear.
"""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import json
import logging
import mimetypes
import os
import secrets
import time
from pathlib import Path

# Before anything imports the bridge: a server has no use for Qt, and must
# behave the same whether or not PySide6 happens to be installed.
os.environ.setdefault("MTGFISH_NO_QT", "1")

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse, Response, StreamingResponse  # noqa: E402

from .auth.routes import AUTH_COOKIE  # noqa: E402
from .jobs import QueueFull  # noqa: E402
from .sessions import WEB, Api, ServerFull, SessionManager, Versions  # noqa: E402
from .settings import Settings  # noqa: E402

log = logging.getLogger("mtgfish.web")

COOKIE = "mtgfish_sid"

_COMPRESSIBLE = ("text/", "application/javascript", "application/json", "image/svg+xml")

_CSP = (
    "default-src 'self'; "
    "img-src 'self' data: https://cards.scryfall.io https://api.scryfall.com "
    "https://svgs.scryfall.io https://cdn.discordapp.com; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


class AppState:
    """Everything a request needs, built once per process."""

    def __init__(self, settings: Settings, accounts=None) -> None:
        self.settings = settings
        if accounts is None:
            from .auth import Accounts
            from .db import Database

            accounts = Accounts(Database())
        self.accounts = accounts
        accounts.on_forget = self.forget_user
        self._start_purging()
        self.api = Api(settings)
        self.sessions = SessionManager(self.api, settings)
        self.sessions.on_finished = self.job_finished
        self.versions = Versions()
        self.gzip_cache: dict[tuple[str, str], bytes] = {}
        self.started = time.time()
        #: Set by whatever serves the app, so /api/_shutdown can stop it.
        self.stop_server = None

    def bind_user(self, session, user_id: str | None) -> None:
        """Make a browser session act as ``user_id`` (or as nobody).

        Its sandbox verdicts follow: the account's own when signed in, a
        fresh private set when not - never a store shared between people.
        """
        if session.user_id == user_id:
            return
        from .auth.verdicts import DbVerdictStore, MemoryVerdictStore, use_verdicts

        session.user_id = user_id
        store = (
            DbVerdictStore(self.accounts.db, user_id) if user_id else MemoryVerdictStore()
        )
        session._thread.submit(use_verdicts, session.bridge, store).result(timeout=30)

    def job_finished(self, session, signal: str, status, payload: dict) -> None:
        """A signed-in, verified person's finished run goes into their history."""
        from .jobs import JobStatus
        from .library import record_run

        if signal != "run_finished" or status is not JobStatus.DONE or not session.user_id:
            return
        config = getattr(session.bridge, "_config", None)
        if config is None:
            return
        if record_run(self.accounts.db, session.user_id, config, payload):
            session.publish("history_changed", [])

    def _start_purging(self) -> None:
        """Delete expired sessions and tokens, and stale unverified accounts,
        now and every six hours - what the Privacy Policy promises."""
        import threading

        from .auth.service import purge_expired

        def purge() -> None:
            try:
                with self.accounts.db.session() as db:
                    removed = purge_expired(db)
                if any(removed.values()):
                    log.info("purged %s", removed)
            except Exception:  # noqa: BLE001 - never take the site down over cleanup
                log.warning("purge failed", exc_info=True)

        def loop(stop=threading.Event()) -> None:
            purge()
            while not stop.wait(6 * 3600):
                purge()

        threading.Thread(target=loop, daemon=True, name="purge").start()

    def forget_user(self, user_id: str) -> None:
        for session in self.sessions.all():
            if session.user_id == user_id:
                self.bind_user(session, None)

    def stop(self) -> None:
        """Shut down gracefully: runs stopped, sessions closed, then the server."""
        self.sessions.close_all()
        if self.stop_server is not None:
            self.stop_server()


def _valid_sid(value: str | None) -> bool:
    return bool(value) and 16 <= len(value) <= 64 and value.replace("-", "").replace(
        "_", ""
    ).isalnum()


def create_app(settings: Settings | None = None, *, accounts=None) -> FastAPI:
    state = AppState(settings or Settings(), accounts)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        # Runs stopped and their worker processes gone before exiting, rather
        # than the interpreter waiting at exit for games nobody will see.
        await asyncio.to_thread(state.sessions.close_all)

    app = FastAPI(
        title="MTG Goldfisher",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.mtgfish = state

    # -- plumbing -----------------------------------------------------------

    def sid_of(request: Request) -> tuple[str, bool]:
        """This browser's session id, and whether it was just issued."""
        value = request.cookies.get(COOKIE)
        if _valid_sid(value):
            return value, False
        return secrets.token_urlsafe(24), True

    def secure(request: Request) -> bool:
        return state.settings.secure_cookies or (
            request.headers.get("x-forwarded-proto", "").lower() == "https"
        )

    def set_cookie(response: Response, request: Request, sid: tuple[str, bool]) -> None:
        if sid[1]:
            response.set_cookie(
                COOKIE, sid[0], max_age=2592000, path="/", httponly=True,
                samesite="lax", secure=secure(request),
            )

    def json_reply(status: int, payload, request: Request | None = None, sid=None) -> Response:
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        response = Response(
            body, status_code=status, media_type="application/json; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )
        if request is not None and sid is not None:
            set_cookie(response, request, sid)
        return response

    from .auth.routes import build_router

    app.include_router(build_router(state.accounts))
    from .library import build_router as library_router

    app.include_router(library_router(state.accounts))

    allowed_origins = {state.accounts.settings.public_base_url.rstrip("/")}
    production = state.accounts.settings.production
    allow_indexing = os.environ.get("MTGFISH_ALLOW_INDEXING", "") not in ("", "0")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        # Cross-site requests that change something are refused outright:
        # a browser always says where a POST came from.
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and origin != "null":
                scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
                own = f"{scheme}://{request.headers.get('host', '')}"
                if origin.rstrip("/") not in allowed_origins | {own}:
                    return JSONResponse({"error": "cross-site request refused"}, status_code=403)
        response = await call_next(request)
        if not allow_indexing:
            # A private beta: search engines should neither index nor follow.
            response.headers.setdefault("X-Robots-Tag", "noindex, nofollow, noarchive")
        if production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    def _session_and_user(sid: str, token: str | None):
        session = state.sessions.get(sid)
        user = state.accounts.user_for(token)
        state.bind_user(session, user.id if user else None)
        return session

    async def session_for(request: Request):
        sid = sid_of(request)
        try:
            session = await asyncio.to_thread(
                _session_and_user, sid[0], request.cookies.get(AUTH_COOKIE)
            )
        except ServerFull:
            return None, sid
        return session, sid

    # -- small endpoints ----------------------------------------------------

    @app.get("/healthz")
    async def healthz():
        return json_reply(200, {
            "ok": True,
            "sessions": state.sessions.count,
            "busy": state.sessions.runs > 0,
            "queued": state.sessions.jobs.queued,
            "version": state.versions.current,
            "uptime": round(time.time() - state.started),
        })

    @app.get("/api/version")
    async def version():
        return json_reply(200, {"version": state.versions.current})

    @app.get("/api/methods")
    async def methods():
        return json_reply(200, {
            "methods": state.api.methods,
            "signals": state.api.signals,
            "version": state.versions.current,
        })

    @app.post("/api/_shutdown")
    async def shutdown(request: Request):
        """The ``--reload`` supervisor asking for a graceful restart.

        Only the supervisor knows the token, and only from this machine; to
        anyone else the endpoint does not exist.
        """
        token = os.environ.get("MTGFISH_SUPERVISOR_TOKEN", "")
        offered = request.headers.get("x-supervisor-token", "")
        host = request.client.host if request.client else ""
        if not token or host not in ("127.0.0.1", "::1") or not secrets.compare_digest(
            offered, token
        ):
            return json_reply(404, {"error": "no such endpoint: /api/_shutdown"})
        asyncio.get_running_loop().run_in_executor(None, state.stop)
        return json_reply(202, {"stopping": True})

    # -- slots --------------------------------------------------------------

    @app.post("/api/call/{method}")
    async def call(method: str, request: Request):
        raw = await request.body()
        if len(raw) > 5_000_000:
            return json_reply(413, {"error": "request too large"})
        # A JSON content type cannot be sent cross-site without a CORS
        # preflight, which this server never approves - so a page on another
        # site cannot drive someone's session with their cookie.
        if "application/json" not in request.headers.get("content-type", ""):
            return json_reply(415, {"error": "send JSON"})
        if method not in state.api.methods:
            return json_reply(404, {"error": f"the server has no method {method!r}"})
        try:
            body = json.loads(raw or b"{}")
            args = body.get("args", []) if isinstance(body, dict) else []
            if not isinstance(args, list):
                raise ValueError("args must be a list")
        except ValueError as exc:
            return json_reply(400, {"error": f"bad request: {exc}"})

        session, sid = await session_for(request)
        if session is None:
            return json_reply(503, {"error": "the server is full right now - try again shortly"},
                              request, sid)

        # JSON has one number type; a slot declared ``int`` must not get 5.0.
        args = [int(a) if isinstance(a, float) and a.is_integer() else a for a in args]
        timeout = state.settings.call_timeout
        try:
            if method.startswith("start_"):
                reply = await asyncio.to_thread(session.start_job, method, args, timeout)
            elif method == "cancel_run":
                reply = await asyncio.to_thread(session.cancel_jobs)
            else:
                future = session.submit(method, args)
                reply = await asyncio.wait_for(asyncio.wrap_future(future), timeout)
        except QueueFull as exc:
            return json_reply(200, {"error": str(exc)}, request, sid)
        except TypeError as exc:
            return json_reply(400, {"error": f"{method}: {exc}"}, request, sid)
        except (TimeoutError, asyncio.TimeoutError):
            return json_reply(504, {"error": f"{method} took too long"}, request, sid)
        except Exception as exc:  # noqa: BLE001 - the page needs to hear about it
            log.exception("slot %s failed", method)
            return json_reply(500, {"error": f"{type(exc).__name__}: {exc}"}, request, sid)

        body = reply.encode() if isinstance(reply, str) else json.dumps(reply).encode()
        return json_reply(200, body, request, sid)

    @app.post("/api/runs/{run_id}/open")
    async def open_run(run_id: str, request: Request):
        """A past run on this browser's Results screen, replays included.

        Replays rebuild games from the run's config, so the browser's bridge
        is given that config - exactly what the run was.
        """
        from .auth.service import user_for_session
        from .models import Run
        from ..sim.runner import RunConfig

        if "application/json" not in request.headers.get("content-type", ""):
            return json_reply(415, {"error": "send JSON"})
        session, sid = await session_for(request)
        if session is None:
            return json_reply(503, {"error": "the server is full right now"}, request, sid)

        def load():
            with state.accounts.db.session() as db:
                user = user_for_session(db, request.cookies.get(AUTH_COOKIE))
                if user is None:
                    return None
                run = db.query(Run).filter(Run.id == run_id, Run.user_id == user.id).one_or_none()
                return (run.config, run.report) if run else None

        found = await asyncio.to_thread(load)
        if found is None:
            return json_reply(404, {"error": "no such run"}, request, sid)
        config_json, report_json = found
        raw = json.loads(config_json)
        config = RunConfig(
            decklists=tuple(raw["decklists"]),
            games=raw["games"],
            max_rounds=raw.get("max_rounds", 25),
            run_seed=raw["run_seed"],
            hero=raw.get("hero", 0),
            card_db_hash=raw.get("card_db_hash", ""),
        )

        def install():
            session.bridge._config = config
            session.bridge._result = None
            session.bridge._report = None

        await asyncio.wrap_future(session._thread.submit(install))
        return json_reply(200, {"report": json.loads(report_json) if report_json else None},
                          request, sid)

    # -- jobs ---------------------------------------------------------------

    @app.get("/api/jobs")
    async def jobs(request: Request):
        session, sid = await session_for(request)
        if session is None:
            return json_reply(503, {"error": "the server is full right now"}, request, sid)
        queue = state.sessions.jobs
        rows = [job.as_dict(queue.position(job)) for job in queue.for_owner(session.owner)]
        return json_reply(200, {"jobs": rows}, request, sid)

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str, request: Request):
        if "application/json" not in request.headers.get("content-type", ""):
            return json_reply(415, {"error": "send JSON"})
        session, sid = await session_for(request)
        if session is None:
            return json_reply(503, {"error": "the server is full right now"}, request, sid)
        queue = state.sessions.jobs
        job = queue.get(job_id)
        # Someone else's job is reported exactly like no job at all.
        if job is None or job.owner != session.owner:
            return json_reply(404, {"error": "no such job"}, request, sid)
        cancelled = await asyncio.to_thread(queue.cancel, job_id)
        return json_reply(200, {"cancelled": cancelled}, request, sid)

    # -- events -------------------------------------------------------------

    @app.get("/api/events")
    async def events(request: Request):
        session, sid = await session_for(request)
        if session is None:
            return json_reply(503, {"error": "the server is full right now"}, request, sid)
        last = request.headers.get("last-event-id")
        subscriber = session.subscribe(
            int(last) if last and last.isdigit() else None, loop=asyncio.get_running_loop()
        )

        async def stream():
            try:
                hello = json.dumps({"version": state.versions.current})
                yield f"event: hello\ndata: {hello}\nretry: 3000\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        # Short: a closed tab is only noticed when the stream
                        # is checked, and that starts the clock on its run.
                        event_id, payload = await asyncio.wait_for(subscriber.queue.get(), 5)
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    yield f"id: {event_id}\ndata: {payload}\n\n"
            finally:
                session.unsubscribe(subscriber)

        response = StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",  # nginx: do not buffer the stream
            },
        )
        set_cookie(response, request, sid)
        return response

    # -- files --------------------------------------------------------------

    @app.get("/robots.txt")
    async def robots():
        if allow_indexing:
            body = "User-agent: *\nAllow: /\n"
        else:
            body = "User-agent: *\nDisallow: /\n"
        return Response(body, media_type="text/plain; charset=utf-8",
                        headers={"Cache-Control": "public, max-age=3600"})

    def legal_page(name: str) -> Response:
        import html

        from .auth.service import TERMS_VERSION

        settings_ = state.accounts.settings
        values = {
            "OPERATOR_NAME": os.environ.get("OPERATOR_NAME") or "the operator of this site",
            "CONTACT_EMAIL": os.environ.get("CONTACT_EMAIL") or "(contact address not set)",
            "SITE_URL": settings_.public_base_url,
            "TERMS_VERSION": TERMS_VERSION,
        }
        text = (WEB / "legal" / f"{name}.html").read_text(encoding="utf8")
        for key, value in values.items():
            text = text.replace("{{" + key + "}}", html.escape(value))
        return Response(text, media_type="text/html; charset=utf-8", headers={
            "Content-Security-Policy": _CSP,
            "X-Frame-Options": "DENY",
            "Cache-Control": "no-cache",
        })

    @app.get("/privacy")
    async def privacy():
        return legal_page("privacy")

    @app.get("/terms")
    async def terms():
        return legal_page("terms")

    @app.api_route("/{path:path}", methods=["GET", "HEAD"])
    async def static(path: str, request: Request):
        if path.startswith("api/"):
            return json_reply(404, {"error": f"no such endpoint: /{path}"})
        if path.startswith("legal/") and path.endswith(".html"):
            # The templates are served filled in, at /privacy and /terms.
            return json_reply(404, {"error": "not found"})
        relative = path.lstrip("/") or "index.html"
        target = (WEB / relative).resolve()
        if not target.is_relative_to(WEB) or not target.is_file():
            return json_reply(404, {"error": "not found"})
        return serve_file(target, relative, request)

    def serve_file(target: Path, relative: str, request: Request) -> Response:
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
        sid = sid_of(request) if relative == "index.html" else None

        if request.headers.get("if-none-match") == etag:
            response = Response(status_code=304, headers=headers)
        else:
            body = target.read_bytes()
            if (
                content_type.startswith(_COMPRESSIBLE)
                and len(body) > 1024
                and "gzip" in request.headers.get("accept-encoding", "")
            ):
                key = (str(target), etag)
                compressed = state.gzip_cache.get(key)
                if compressed is None:
                    compressed = gzip.compress(body, compresslevel=6)
                    state.gzip_cache = {
                        k: v for k, v in state.gzip_cache.items() if k[0] != str(target)
                    }
                    state.gzip_cache[key] = compressed
                body = compressed
                headers["Content-Encoding"] = "gzip"
            response = Response(body, media_type=content_type, headers=headers)
            if request.method == "HEAD":
                response = Response(status_code=200, media_type=content_type, headers=headers)
        if sid is not None:
            set_cookie(response, request, sid)
        return response

    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        return JSONResponse({"error": f"no such endpoint: {request.url.path}"}, status_code=404)

    return app
