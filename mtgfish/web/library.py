"""Saved decks and simulation history: the /api/decks and /api/runs routes.

Every query here is scoped to the signed-in user in this module, because the
database has no row-level security of its own: a deck or run id belonging to
someone else is answered exactly like an id that does not exist (404), so the
API never confirms that another person's record exists.

=====================================  =====================================
``GET  /api/decks``                    your decks, newest first
``POST /api/decks``                    save a deck: name + decklist
``GET  /api/decks/{id}``               one deck, its latest list, versions
``POST /api/decks/{id}``               rename, and/or save a new version
``POST /api/decks/{id}/delete``        delete it (its runs stay, unlinked)
``GET  /api/runs``                     your simulation history
``GET  /api/runs/{id}``                one run: config and full report
``POST /api/runs/{id}/delete``         remove it from history
=====================================  =====================================

Saving needs a verified email or a Discord login (``require_verified``);
reading your own (possibly empty) lists does not.
"""

from __future__ import annotations

import hashlib
import json
import re

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from .auth.routes import AUTH_COOKIE, CSRF_COOKIE
from .auth.service import AuthError, require_verified, user_for_session
from .models import Deck, DeckVersion, Run, now

MAX_DECKLIST = 60_000
MAX_DECKS = 200


def text_hash(decklist: str) -> str:
    """A hash of a list that ignores blank-line and trailing-space noise."""
    lines = [line.rstrip() for line in decklist.replace("\r\n", "\n").split("\n")]
    normalized = "\n".join(line for line in lines if line.strip())
    return hashlib.sha256(normalized.encode("utf8")).hexdigest()


def commander_of(decklist: str) -> str | None:
    """The commander line(s) of a list, for showing in a table."""
    lines = decklist.replace("\r\n", "\n").split("\n")
    names, inside = [], False
    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith("//"):
            inside = "commander" in stripped.lower()
            continue
        if inside and stripped:
            names.append(re.sub(r"^\d+x?\s+", "", stripped))
    return " + ".join(names)[:200] or None


def deck_row(deck: Deck, latest: DeckVersion | None) -> dict:
    return {
        "id": deck.id,
        "name": deck.name,
        "commander": deck.commander,
        "created_at": deck.created_at.isoformat(),
        "updated_at": deck.updated_at.isoformat(),
        "version": latest.number if latest else 0,
    }


def run_row(run: Run, deck_names: dict[str, str]) -> dict:
    config = json.loads(run.config)
    return {
        "id": run.id,
        "label": run.label,
        "status": run.status,
        "win_rate": run.win_rate,
        "games": run.games,
        "run_seed": run.run_seed,
        "max_rounds": config.get("max_rounds"),
        "card_db_hash": run.card_db_hash,
        "deck_version_id": run.deck_version_id,
        "deck": deck_names.get(run.deck_version_id or ""),
        "created_at": run.created_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


def latest_version(db, deck: Deck) -> DeckVersion | None:
    return db.scalar(
        select(DeckVersion)
        .where(DeckVersion.deck_id == deck.id)
        .order_by(DeckVersion.number.desc())
        .limit(1)
    )


def version_for_text(db, user_id: str, decklist: str) -> DeckVersion | None:
    """The newest of this user's saved versions holding exactly this list."""
    return db.scalar(
        select(DeckVersion)
        .join(Deck, Deck.id == DeckVersion.deck_id)
        .where(Deck.user_id == user_id, DeckVersion.text_hash == text_hash(decklist))
        .order_by(DeckVersion.created_at.desc())
        .limit(1)
    )


def record_run(database, user_id: str, config, report_payload: dict, status: str = "done") -> str | None:
    """Store a finished run in a user's history. Returns its id.

    ``config`` is the ``RunConfig`` the run was: decklists, seed, games,
    max_rounds, hero and card_db_hash - everything ``sim.runner.replay`` needs
    to play any of its games again.
    """
    from .models import User

    with database.session() as db:
        user = db.get(User, user_id)
        if user is None or not (user.email_verified or user.discord_id):
            return None
        hero_list = config.decklists[config.hero] if config.decklists else ""
        version = version_for_text(db, user_id, hero_list)
        deck = db.get(Deck, version.deck_id) if version else None
        label = (
            f"{deck.name} v{version.number}" if deck and version
            else (commander_of(hero_list) or "Unsaved deck")
        )
        run = Run(
            user_id=user_id,
            deck_version_id=version.id if version else None,
            label=label[:200],
            status=status,
            config=json.dumps(
                {
                    "decklists": list(config.decklists),
                    "games": config.games,
                    "max_rounds": config.max_rounds,
                    "run_seed": config.run_seed,
                    "hero": config.hero,
                    "card_db_hash": config.card_db_hash,
                }
            ),
            report=json.dumps(report_payload),
            win_rate=report_payload.get("win_rate"),
            games=config.games,
            run_seed=config.run_seed,
            card_db_hash=config.card_db_hash or "",
            finished_at=now(),
        )
        db.add(run)
        db.commit()
        return run.id


def build_router(accounts) -> APIRouter:
    router = APIRouter(prefix="/api")
    database = accounts.db

    def reply(payload, status: int = 200) -> JSONResponse:
        return JSONResponse(payload, status_code=status, headers={"Cache-Control": "no-store"})

    def refuse(exc: AuthError) -> JSONResponse:
        return reply({"error": str(exc)}, exc.status)

    def check_csrf(request: Request) -> None:
        import secrets

        cookie = request.cookies.get(CSRF_COOKIE, "")
        header = request.headers.get("x-csrf-token", "")
        if not cookie or not secrets.compare_digest(cookie, header):
            raise AuthError("the page is out of date - reload and try again", 403)
        if "application/json" not in request.headers.get("content-type", ""):
            raise AuthError("send JSON", 415)

    async def body(request: Request) -> dict:
        try:
            data = await request.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def signed_in(db, request: Request):
        user = user_for_session(db, request.cookies.get(AUTH_COOKIE))
        if user is None:
            raise AuthError("log in to see your decks and history", 401)
        return user

    def own_deck(db, user, deck_id: str) -> Deck:
        deck = db.scalar(select(Deck).where(Deck.id == deck_id, Deck.user_id == user.id))
        if deck is None:
            raise AuthError("no such deck", 404)
        return deck

    def own_run(db, user, run_id: str) -> Run:
        run = db.scalar(select(Run).where(Run.id == run_id, Run.user_id == user.id))
        if run is None:
            raise AuthError("no such run", 404)
        return run

    def clean_list(data: dict) -> str:
        decklist = str(data.get("decklist", ""))
        if not decklist.strip():
            raise AuthError("the decklist is empty")
        if len(decklist) > MAX_DECKLIST:
            raise AuthError("that decklist is too long")
        return decklist

    # -- decks --------------------------------------------------------------

    @router.get("/decks")
    def list_decks(request: Request):
        try:
            with database.session() as db:
                user = signed_in(db, request)
                decks = db.scalars(
                    select(Deck).where(Deck.user_id == user.id).order_by(Deck.updated_at.desc())
                ).all()
                return reply({"decks": [deck_row(d, latest_version(db, d)) for d in decks]})
        except AuthError as exc:
            return refuse(exc)

    @router.post("/decks")
    async def create_deck(request: Request):
        data = await body(request)
        try:
            check_csrf(request)
            with database.session() as db:
                user = require_verified(signed_in(db, request))
                count = db.scalar(select(func.count()).select_from(Deck).where(Deck.user_id == user.id))
                if count >= MAX_DECKS:
                    raise AuthError(f"you can keep up to {MAX_DECKS} decks")
                decklist = clean_list(data)
                name = str(data.get("name", "")).strip()[:120] or commander_of(decklist) or "Untitled deck"
                deck = Deck(user_id=user.id, name=name, commander=commander_of(decklist))
                db.add(deck)
                db.flush()
                version = DeckVersion(
                    deck_id=deck.id, number=1, decklist=decklist, text_hash=text_hash(decklist)
                )
                db.add(version)
                db.commit()
                return reply({"deck": deck_row(deck, version)}, 201)
        except AuthError as exc:
            return refuse(exc)

    @router.get("/decks/{deck_id}")
    def get_deck(deck_id: str, request: Request):
        try:
            with database.session() as db:
                user = signed_in(db, request)
                deck = own_deck(db, user, deck_id)
                versions = db.scalars(
                    select(DeckVersion)
                    .where(DeckVersion.deck_id == deck.id)
                    .order_by(DeckVersion.number.desc())
                ).all()
                latest = versions[0] if versions else None
                return reply({
                    "deck": deck_row(deck, latest),
                    "decklist": latest.decklist if latest else "",
                    "versions": [
                        {"id": v.id, "number": v.number, "created_at": v.created_at.isoformat()}
                        for v in versions
                    ],
                })
        except AuthError as exc:
            return refuse(exc)

    @router.get("/decks/{deck_id}/versions/{number}")
    def get_version(deck_id: str, number: int, request: Request):
        try:
            with database.session() as db:
                user = signed_in(db, request)
                deck = own_deck(db, user, deck_id)
                version = db.scalar(
                    select(DeckVersion).where(
                        DeckVersion.deck_id == deck.id, DeckVersion.number == number
                    )
                )
                if version is None:
                    raise AuthError("no such version", 404)
                return reply({"number": version.number, "decklist": version.decklist})
        except AuthError as exc:
            return refuse(exc)

    @router.post("/decks/{deck_id}")
    async def update_deck(deck_id: str, request: Request):
        data = await body(request)
        try:
            check_csrf(request)
            with database.session() as db:
                user = require_verified(signed_in(db, request))
                deck = own_deck(db, user, deck_id)
                latest = latest_version(db, deck)
                if "name" in data:
                    name = str(data.get("name", "")).strip()[:120]
                    if not name:
                        raise AuthError("enter a name")
                    deck.name = name
                if "decklist" in data:
                    decklist = clean_list(data)
                    # Only a changed list is a new version: saving the same
                    # list twice would make "the list a run played" ambiguous.
                    if latest is None or latest.text_hash != text_hash(decklist):
                        latest = DeckVersion(
                            deck_id=deck.id,
                            number=(latest.number + 1) if latest else 1,
                            decklist=decklist,
                            text_hash=text_hash(decklist),
                        )
                        db.add(latest)
                        deck.commander = commander_of(decklist)
                deck.updated_at = now()
                db.commit()
                return reply({"deck": deck_row(deck, latest)})
        except AuthError as exc:
            return refuse(exc)

    @router.post("/decks/{deck_id}/delete")
    async def delete_deck(deck_id: str, request: Request):
        try:
            check_csrf(request)
            with database.session() as db:
                user = signed_in(db, request)
                deck = own_deck(db, user, deck_id)
                db.delete(deck)
                db.commit()
                return reply({"deleted": True})
        except AuthError as exc:
            return refuse(exc)

    # -- runs ---------------------------------------------------------------

    def deck_names(db, runs) -> dict[str, str]:
        ids = {r.deck_version_id for r in runs if r.deck_version_id}
        if not ids:
            return {}
        rows = db.execute(
            select(DeckVersion.id, Deck.name, DeckVersion.number)
            .join(Deck, Deck.id == DeckVersion.deck_id)
            .where(DeckVersion.id.in_(ids))
        ).all()
        return {vid: f"{name} v{number}" for vid, name, number in rows}

    @router.get("/runs")
    def list_runs(request: Request, limit: int = 100):
        try:
            with database.session() as db:
                user = signed_in(db, request)
                runs = db.scalars(
                    select(Run)
                    .where(Run.user_id == user.id)
                    .order_by(Run.created_at.desc())
                    .limit(max(1, min(limit, 500)))
                ).all()
                names = deck_names(db, runs)
                return reply({"runs": [run_row(r, names) for r in runs]})
        except AuthError as exc:
            return refuse(exc)

    @router.get("/runs/{run_id}")
    def get_run(run_id: str, request: Request):
        try:
            with database.session() as db:
                user = signed_in(db, request)
                run = own_run(db, user, run_id)
                row = run_row(run, deck_names(db, [run]))
                row["config"] = json.loads(run.config)
                row["report"] = json.loads(run.report) if run.report else None
                return reply({"run": row})
        except AuthError as exc:
            return refuse(exc)

    @router.post("/runs/{run_id}/delete")
    async def delete_run(run_id: str, request: Request):
        try:
            check_csrf(request)
            with database.session() as db:
                user = signed_in(db, request)
                run = own_run(db, user, run_id)
                db.delete(run)
                db.commit()
                return reply({"deleted": True})
        except AuthError as exc:
            return refuse(exc)

    return router
