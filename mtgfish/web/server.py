"""Serving the web app: uvicorn running the FastAPI app from ``app``.

The application itself lives in ``app`` (routes), ``sessions`` (one bridge
per browser), ``jobs`` (simulations, queued off the request path) and
``settings`` (the environment). This module only puts it on a socket, with
the small ``serve_forever`` / ``shutdown`` surface the command line, the
``--reload`` supervisor and the tests use.

For anything bigger than one machine, run the ASGI app under your own server
instead: ``uvicorn "mtgfish.web.app:create_app" --factory`` (see DEPLOY.md).
"""

from __future__ import annotations

import os
import socket
import threading
import time

os.environ.setdefault("MTGFISH_NO_QT", "1")

from .app import COOKIE, AppState, create_app  # noqa: E402
from .sessions import (  # noqa: E402
    TRANSIENT_SIGNALS,
    Api,
    ServerFull,
    Session,
    SessionManager,
    Versions,
    code_files,
    web_files,
)
from .settings import Settings  # noqa: E402


class Server:
    """One uvicorn server on one socket, startable from any thread."""

    def __init__(self, host: str, port: int, settings: Settings | None = None) -> None:
        import uvicorn

        self.asgi = create_app(settings)
        self.app: AppState = self.asgi.state.mtgfish
        self.app.stop_server = self.shutdown_soon
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        self._socket = socket.socket(family, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((host, port))
        self.server_address = self._socket.getsockname()[:2]
        config = uvicorn.Config(
            self.asgi,
            log_level=os.environ.get("MTGFISH_LOG_LEVEL", "warning"),
            lifespan="on",
            # An open event stream would otherwise hold a shutdown forever.
            timeout_graceful_shutdown=5,
            proxy_headers=True,
            forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"),
        )
        self._uvicorn = uvicorn.Server(config)
        self._stopped = threading.Event()

    def serve_forever(self) -> None:
        try:
            self._uvicorn.run(sockets=[self._socket])
        finally:
            self._stopped.set()

    def shutdown_soon(self) -> None:
        self._uvicorn.should_exit = True

    def shutdown(self, timeout: float = 15.0) -> None:
        self.shutdown_soon()
        self._stopped.wait(timeout)

    def server_close(self) -> None:
        try:
            self._socket.close()
        except OSError:
            pass

    def wait_started(self, timeout: float = 15.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._uvicorn.started:
                return True
            time.sleep(0.05)
        return False


def make_server(host: str, port: int, settings: Settings | None = None) -> Server:
    return Server(host, port, settings)


__all__ = [
    "COOKIE", "TRANSIENT_SIGNALS", "Api", "AppState", "Server", "ServerFull", "Session",
    "SessionManager", "Settings", "Versions", "code_files", "create_app", "make_server",
    "web_files",
]
