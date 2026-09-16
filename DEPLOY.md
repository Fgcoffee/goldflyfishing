# Running it on the web

The desktop app (`python -m mtgfish.ui`) and the web app are the same front
end and the same `Bridge`. The window talks to it over a QWebChannel; a browser
talks to it through `mtgfish.web` over HTTP. `ui/web/transport.js` picks
whichever is there.

## Locally

```bash
python -m mtgfish.web --reload
```

Open http://127.0.0.1:8000. The card database is read from `cache/` as usual.

## In a container

```bash
docker compose up
```

`docker-compose.yml` mounts `./mtgfish` and `./cache` into the container, so
the running site is whatever is in this folder. For an image that carries its
own copy of the code instead:

```bash
docker build -t mtg-goldfisher .
docker run -p 8000:8000 -v /path/holding/cards.sqlite:/data mtg-goldfisher
```

The image has no Qt at all. `MTGFISH_NO_QT=1` makes the bridge use the stand-ins
in `ui/qtcompat.py`, so nothing in `bridge.py` may import PySide6 directly -
import `QObject`, `Signal` and `Slot` from `.qtcompat`.

Put it behind something that terminates TLS (Caddy, nginx, a cloud load
balancer) and set `MTGFISH_SECURE_COOKIES=1`. The Archidekt sign-in sends a
password through this server; it must not travel in the clear. For nginx, turn
off buffering for `/api/events` or progress arrives in lumps
(`proxy_buffering off;` - the server also sends `X-Accel-Buffering: no`). All
URLs the page uses are relative, so serving under a sub-path works.

## Taking the latest version

The folder is edited continuously, so the server is built to follow it:

| what changed | what happens |
|---|---|
| `ui/web/*` (HTML, CSS, JS) | Served from disk on every request with `Cache-Control: no-cache`, so the next load gets it. Open pages check `/api/version` every minute and show *"A newer version is available - Reload"*. Nothing restarts. |
| Python under `mtgfish/` | With `--reload` (or `MTGFISH_RELOAD=1`) a supervisor restarts the server. It **waits for a running simulation to finish first** (up to `MTGFISH_RELOAD_MAX_WAIT` seconds, default fifteen minutes), then asks the server to shut down gracefully - runs stopped, worker processes killed - before resorting to killing it. Open pages reconnect by themselves and offer the reload. |
| A new slot in `bridge.py` | Reachable from the browser after the restart, with no server change. The server asks the bridge what slots it has. |
| A slot the page expects but the server lacks | `Backend.has()` is false; features depending on it hide instead of breaking. |
| A half-saved file that fails to import | The supervisor waits for the next change rather than exiting. |

Deck slots are kept in the browser's local storage, so reloading into a new
version never loses a pasted list.

## Stopping runs

A run stops, and its worker processes are killed rather than left to finish
their games, when any of these happens:

* **Stop run** is pressed (it appears in the status bar while a run is going);
* the page has been closed for `MTGFISH_ABANDON_GRACE` seconds;
* the server shuts down - Ctrl+C, a `--reload` restart, or `docker stop`;
* the desktop window is closed.

Workers also exit on their own if the process that started them dies without
cleaning up (killed from Task Manager, a crash), so nothing is left spinning.

## Settings

| variable | default | meaning |
|---|---|---|
| `PORT`, `MTGFISH_HOST` | `8000`, `127.0.0.1` | where to listen |
| `MTGFISH_DATA_DIR` | `cache/` | holds `cards.sqlite` |
| `MTGFISH_MAX_SESSIONS` | `16` | browser sessions, each with its own bridge (~30 MB each, more once a deck is loaded) |
| `MTGFISH_SESSION_TTL` | `14400` | idle seconds before a session is closed |
| `MTGFISH_MAX_RUNS` | `1` | simulations at once across everyone. A run already uses every core. |
| `MTGFISH_ABANDON_GRACE` | `30` | seconds a page may be gone before its run is stopped; long enough to survive a reload. Negative never stops it. |
| `MTGFISH_RELOAD_MAX_WAIT` | `900` | seconds `--reload` waits for a busy run before restarting anyway |
| `MTGFISH_CALL_TIMEOUT` | `180` | seconds one call may take |
| `MTGFISH_WEB_DENY` | `read_file` | slots the browser may not call, comma separated |
| `MTGFISH_SECURE_COOKIES` | off | mark the session cookie `Secure` (also automatic behind a proxy sending `X-Forwarded-Proto: https`) |

## Before it is public

Three things are fine for a private deployment and not for an open one:

* **Card verdicts are global.** "Wrong - switch it off" in the sandbox writes to
  one `verdicts.json` and changes every user's simulations. Add
  `sandbox_judge` to `MTGFISH_WEB_DENY` until verdicts belong to accounts.
* **Log in / Register and Manage subscription are placeholders.** The dialogs
  send nothing.
* **Sessions are anonymous.** A session is a random cookie; there is no account
  behind it, so no limit per person beyond `MTGFISH_MAX_RUNS`.

## Archidekt import

Archidekt publishes no API and offers no OAuth. The import uses the endpoints
its own site uses:

* `POST /api/rest-auth/login/` with `username` or `email` and `password`,
  returning a token. The password is passed through and never stored; the token
  is held in the server-side session only and never sent to the page.
* `GET /api/decks/v3/?ownerUsername=...` to list decks - public ones for anyone,
  private and unlisted ones too when the token is the owner's.
* `GET /api/decks/{id}/` for the deck itself, as the link import always did.

Being unpublished, these can change without notice. Browsing a user's public
decks and importing by link have been checked against the live site; signing in
has been checked only as far as a rejected login (no real credentials were
used), and the `Authorization: JWT <token>` header is what their site sends,
with `Bearer` tried as a fallback.
