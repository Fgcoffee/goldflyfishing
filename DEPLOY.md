# Running it on the web

The desktop app (`python -m mtgfish.ui`) and the web app are the same front
end and the same `Bridge`. The window talks to it over a QWebChannel; a browser
talks to it through `mtgfish.web` over HTTP. `ui/web/transport.js` picks
whichever is there.

The web server is a FastAPI app served by uvicorn:

| module | what it holds |
|---|---|
| `mtgfish/web/app.py` | the routes (`create_app()` builds the ASGI app) |
| `mtgfish/web/sessions.py` | one `Bridge` per browser, each on its own thread |
| `mtgfish/web/jobs.py` | simulations as jobs: queued, running, done, cancelled, failed |
| `mtgfish/web/settings.py` | every setting, from the environment (and a local `.env`) |
| `mtgfish/web/server.py` | puts the app on a socket for `python -m mtgfish.web` |

## Locally

```bash
pip install -r requirements-web.txt
python -m mtgfish.web --reload
```

Open http://127.0.0.1:8000. The card database is read from `cache/` as usual.
Secrets for local use go in a `.env` file in the folder you start the server
from. It is ignored by version control; never commit it. A variable already
set in the environment always wins over `.env`.

To run the ASGI app under your own uvicorn (or gunicorn with uvicorn workers -
**one worker process only**, since sessions and the job queue live in memory):

```bash
uvicorn "mtgfish.web.app:create_app" --factory --host 0.0.0.0 --port 8000 --proxy-headers
```

## Simulations are jobs

No request waits for a game. `start_run`, `start_lab`, `start_cutlab` and
`start_queue_test` create a job and return at once. If the machine has room the
job starts immediately and the reply is the bridge's own; otherwise it waits in
order and the page is told its place (`job_status` event). Results arrive as
the usual `*_finished` events on `/api/events`, and the job ends with them.

* `GET /api/jobs` lists this browser's jobs; `POST /api/jobs/<id>/cancel` stops
  one (someone else's job id is reported as not found).
* **Stop run** cancels everything the browser has waiting or running.
* Limits: `MTGFISH_MAX_RUNS` at once across everyone, `MTGFISH_MAX_RUNS_PER_USER`
  at once per browser, `MTGFISH_MAX_QUEUED_PER_USER` waiting per browser.

The queue is behind an interface (`jobs.JobQueue`). `LocalJobQueue` plays jobs
on this machine through the browser's bridge and its process pool. To move
simulations to separate worker machines, implement the same interface over RQ,
Celery or Dramatiq: enqueue the job's `RunConfig` (everything a run is), let a
worker play it, and call `complete()` with the result so the finish event
reaches the page.

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
| `MTGFISH_MAX_RUNS` | `1` | simulations at once across everyone. A run already uses every core; more wait in the queue. |
| `MTGFISH_MAX_RUNS_PER_USER` | `1` | simulations one browser may have running at once |
| `MTGFISH_MAX_QUEUED_PER_USER` | `2` | simulations one browser may have waiting; past this a start is refused |
| `PUBLIC_BASE_URL` | `http://127.0.0.1:8000` | the address people use to reach the site (OAuth redirects are built from it) |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | proxies trusted to set `X-Forwarded-*` headers |
| `MTGFISH_LOG_LEVEL` | `warning` | uvicorn's log level (`--verbose` sets `info`) |
| `MTGFISH_ABANDON_GRACE` | `30` | seconds a page may be gone before its run is stopped; long enough to survive a reload. Negative never stops it. |
| `MTGFISH_RELOAD_MAX_WAIT` | `900` | seconds `--reload` waits for a busy run before restarting anyway |
| `MTGFISH_CALL_TIMEOUT` | `180` | seconds one call may take |
| `MTGFISH_WEB_DENY` | `read_file` | slots the browser may not call, comma separated |
| `MTGFISH_SECURE_COOKIES` | off | mark the session cookie `Secure` (also automatic behind a proxy sending `X-Forwarded-Proto: https`) |

## Accounts

Built into the app (no auth vendor): email + password, and Discord. Anonymous
use keeps working for simulations; signing in adds per-account sandbox
verdicts and per-account run limits (and, next, saved decks and history).

| route | |
|---|---|
| `GET /auth/me` | the signed-in user, or 401 |
| `POST /auth/register`, `/auth/login`, `/auth/logout` | email + password |
| `POST /auth/verify`, `/auth/verify/resend` | email verification |
| `POST /auth/password/forgot`, `/auth/password/reset` | password reset |
| `POST /auth/account`, `/auth/account/delete` | display name; delete everything |
| `GET /auth/discord/login`, `/auth/discord/callback` | Discord sign-in (`?link=1` links it) |
| `POST /auth/discord/unlink` | refused if it would leave no way to log in |

How it behaves:

* **Passwords**: argon2id, at least 10 characters, common ones refused. A wrong
  email and a wrong password get the same answer; a reset request never says
  whether the address has an account.
* **Email links** (verify: 48 h, reset: 30 min) are random, single use, and only
  their hash is stored. They land on the page (`/#verify=…`, `/#reset=…`), which
  redeems them, so a mail scanner opening the link does not use it up.
  Resetting a password signs that account out everywhere.
* **Unverified accounts** can log in, but cannot save decks or runs until the
  email is verified (or Discord is linked).
* **Discord**: OAuth2 authorization code with PKCE (S256), scopes
  `identify email`, a random `state` in a signed ten-minute cookie. Discord's
  tokens are used for the token exchange and `/users/@me`, then dropped. A
  Discord login joins an existing account only when Discord reports a
  *verified* email equal to that account's *verified* email.
* **Sessions**: a random id in an `HttpOnly`, `SameSite=Lax` cookie (`Secure` in
  production); only its hash is stored, with a 30-day expiry. Logout deletes it.
* **CSRF**: POSTs to `/auth/*` need the `X-CSRF-Token` header to match the
  `mtgfish_csrf` cookie, and any state-changing request whose `Origin` is
  another site is refused.
* **Rate limits** on all of `/auth/*`, tighter on register, login, reset and the
  Discord routes (in memory: one server process).
* **Deleting an account** removes the user, sessions, tokens, verdicts and (once
  they exist) decks and runs.
* **Sandbox verdicts** belong to the account, in the database. Anonymous
  browsers get a private set that lasts as long as the session. The desktop
  app's `verdicts.json` is never used by the server.

### Configuration

All from the environment (or a local `.env`, which is never committed):

| variable | meaning |
|---|---|
| `MTGFISH_ENV` | `production` turns on the strict checks: it requires `SESSION_SECRET`, an `https://` `PUBLIC_BASE_URL`, and email delivery; it forces `Secure` cookies; and it refuses the fake Discord login |
| `SESSION_SECRET` | 32+ random characters; signs the OAuth state cookie. `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `DATABASE_URL` | `postgresql+psycopg://user:pass@host/db` in production; default `sqlite:///$MTGFISH_DATA_DIR/web.sqlite` |
| `PUBLIC_BASE_URL` | e.g. `https://goldfisher.example` - redirect URIs and email links are built from it |
| `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET` | the Discord application. Without a secret, development gets a fake "Dev Discord User" login |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` | email by SMTP (STARTTLS on 587, TLS on 465) |
| `RESEND_API_KEY` | or email through Resend |
| `MAIL_FROM` | sender, e.g. `Goldfisher <no-reply@goldfisher.example>`. With no mail settings, development writes emails (links included) to the log |

### Discord redirect URIs

Register each one exactly, under OAuth2 → Redirects in the Discord developer
portal:

* local: `http://127.0.0.1:8000/auth/discord/callback`
* production: `https://<your domain>/auth/discord/callback` (add it once the
  domain exists; `PUBLIC_BASE_URL` must match it)

Keep "Public Client" off: the secret stays on the server.

### The database

The schema is owned by Alembic (`mtgfish/web/migrations`) and the server
migrates at start-up. By hand: `alembic upgrade head` from the repository, with
`DATABASE_URL` set. Run one server process per database for now: sessions and
rate limits live in memory.

## Before it is public

* **Manage subscription is a placeholder.** The dialog sends nothing.
* **One process.** Rate limits, the job queue and browser sessions are in
  memory; scaling out needs Redis (or similar) behind them first.

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
