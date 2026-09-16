"""The web server, over real HTTP on a free port.

What matters most here is what must *not* work: a slot that reads files off
the server's disk, a path that climbs out of the web folder, a cross-site form
post. The rest is that the browser gets the same bridge the desktop app does.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from mtgfish.paths import card_db_path


@pytest.fixture(scope="module")
def base_url():
    if not card_db_path().exists():
        pytest.skip("card database not built")
    from mtgfish.web.server import make_server

    server = make_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


class Client:
    """Keeps the session cookie, as a browser would."""

    def __init__(self, base: str) -> None:
        self.base = base
        self.cookie = ""

    def request(self, path: str, body=None, content_type="application/json", method=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", content_type)
        if self.cookie:
            req.add_header("Cookie", self.cookie)
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                self._keep(response)
                return response.status, response.read(), response.headers
        except urllib.error.HTTPError as error:
            return error.code, error.read(), error.headers

    def _keep(self, response) -> None:
        cookie = response.headers.get("Set-Cookie")
        if cookie:
            self.cookie = cookie.split(";")[0]

    def call(self, method: str, *args):
        status, body, _ = self.request(f"/api/call/{method}", {"args": list(args)})
        return status, json.loads(body)


@pytest.fixture
def client(base_url):
    return Client(base_url)


def test_every_bridge_slot_is_reachable_except_reading_files(client):
    status, body, _ = client.request("/api/methods")
    info = json.loads(body)
    assert status == 200
    assert {"validate_deck", "deck_view", "start_run", "sandbox_inspect"} <= set(info["methods"])
    assert "read_file" not in info["methods"]
    assert "run_finished" in info["signals"]


def test_read_file_is_refused(client):
    status, body = client.call("read_file", "pyproject.toml")
    assert status == 404
    assert "read_file" in body["error"]


def test_a_slot_answers_with_its_own_json(client):
    status, body = client.call("deck_view", "1 Sol Ring\n\n// Commander\n1 Atraxa, Praetors' Voice")
    assert status == 200
    assert body["commanders"][0]["name"] == "Atraxa, Praetors' Voice"


def test_a_session_keeps_its_state_between_calls(client):
    client.call("sandbox_reset")
    client.call("sandbox_put", "Grizzly Bears", "battlefield", 0)
    _, state = client.call("sandbox_state")
    assert [o["name"] for o in state["players"][0]["battlefield"]] == ["Grizzly Bears"]

    stranger = Client(client.base)
    _, theirs = stranger.call("sandbox_state")
    assert theirs["players"][0]["battlefield"] == []


def test_a_form_post_from_another_site_is_refused(client):
    status, _, _ = client.request("/api/call/sandbox_reset", {"args": []},
                                  content_type="application/x-www-form-urlencoded")
    assert status == 415


def test_wrong_arguments_come_back_as_an_error_not_a_crash(client):
    """The bridge's own guard reports it, exactly as it would in the window."""
    status, body = client.call("deck_view")
    assert status in (200, 400)
    assert "error" in body

    status, body = client.call("sandbox_state")  # the session still works after
    assert status == 200 and "players" in body


def test_static_files_revalidate_and_cannot_escape(client):
    status, body, headers = client.request("/")
    assert status == 200 and b"transport.js" in body
    assert headers["Cache-Control"] == "no-cache"
    assert "Content-Security-Policy" in headers

    req = urllib.request.Request(client.base + "/style.css", headers={"If-None-Match": headers.get("ETag", "")})
    status, _, css_headers = client.request("/style.css")
    assert status == 200
    req = urllib.request.Request(client.base + "/style.css", headers={"If-None-Match": css_headers["ETag"]})
    with pytest.raises(urllib.error.HTTPError) as not_modified:
        urllib.request.urlopen(req, timeout=10)
    assert not_modified.value.code == 304

    status, _, _ = client.request("/../pyproject.toml")
    assert status == 404
    status, _, _ = client.request("/%2e%2e/pyproject.toml")
    assert status == 404


def test_events_are_replayed_to_a_page_that_reconnects(base_url):
    """A run finishing while the page's connection drops must still arrive."""
    from mtgfish.web.server import App, Session, Settings

    app = App(Settings())
    session = Session("x" * 24, app.api, app.sessions)
    session.publish("run_finished", ['{"games": 1}'])
    session.publish("progressed", [1, 2])
    replay = session.subscribe(last_event_id=0)
    event_id, payload = replay.get_nowait()
    assert json.loads(payload)["signal"] == "run_finished"
    assert replay.empty()  # progress ticks are not worth replaying
    session.close()


def test_stopping_when_nothing_runs_says_so(client):
    status, body = client.call("cancel_run")
    assert status == 200 and body["cancelled"] is False


def _session_with_fake_run(grace: int):
    from mtgfish.web.server import App, Session, Settings

    app = App(Settings(abandon_grace=grace))
    session = Session(secrets_token(), app.api, app.sessions)
    stopped: list[bool] = []
    session.bridge.cancel_run = lambda: stopped.append(True) or '{"cancelled": true}'
    session.running = True
    return session, stopped


def secrets_token() -> str:
    import secrets

    return secrets.token_urlsafe(24)


def test_a_run_stops_when_its_page_stays_closed(base_url):
    import time

    session, stopped = _session_with_fake_run(grace=0)
    session.unsubscribe(session.subscribe(None))
    time.sleep(0.5)
    assert stopped == [True]
    session.running = False
    session.close()


def test_reloading_the_page_does_not_stop_the_run(base_url):
    """The stream drops on reload too; coming back in time keeps the run."""
    import time

    session, stopped = _session_with_fake_run(grace=1)
    session.unsubscribe(session.subscribe(None))
    session.subscribe(None)  # the reloaded page reconnects
    time.sleep(1.5)
    assert stopped == []
    session.running = False
    session.close()


def test_the_shutdown_endpoint_is_invisible_without_the_token(client):
    status, _, _ = client.request("/api/_shutdown", {}, method="POST")
    assert status == 404


def test_health_reports_the_version(client):
    status, body, _ = client.request("/healthz")
    info = json.loads(body)
    assert status == 200 and info["ok"] and info["version"]
