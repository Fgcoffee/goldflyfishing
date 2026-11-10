"""Accounts: what registering, signing in, verifying and resetting *do*.

Kept apart from the HTTP routes so the rules can be read (and tested) as
plain functions. Every function takes an open database session and leaves
committing to the caller, except where noted.

Rules worth knowing:

* Wrong email and wrong password fail identically ("wrong email or
  password"), in the same time - an unknown email is still checked against
  a dummy hash.
* Asking for a reset never says whether the address has an account.
* Tokens (verification, reset) are random, single use, expiring, and stored
  only as a SHA-256 hash.
* Resetting a password signs out every session of that account.
* Unverified accounts may sign in; saving decks and runs needs a verified
  email (``require_verified``) or a Discord login.
* A Discord login joins an existing account only when Discord says its email
  is verified and that email is the account's *verified* email.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import secrets
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import AuthSession, AuthToken, User, now
from . import passwords
from .discord import DiscordProfile

#: Bump when the Terms or the Privacy Policy change in a way people must
#: agree to again; the date of the change is the usual choice.
TERMS_VERSION = "2026-11-10"

#: Unverified email accounts older than this are deleted (Privacy Policy,
#: "How long we keep data").
UNVERIFIED_DAYS = 30

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
GENERIC_LOGIN_ERROR = "wrong email or password"


class AuthError(Exception):
    """A refusal to show the person, with an HTTP status."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf8")).hexdigest()


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _aware(moment: dt.datetime) -> dt.datetime:
    # SQLite hands timezone-aware columns back naive; they were stored in UTC.
    return moment if moment.tzinfo else moment.replace(tzinfo=dt.UTC)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def create_session(db: Session, user: User, days: int) -> str:
    """Sign a user in. Returns the cookie value; only its hash is stored."""
    token = secrets.token_urlsafe(32)
    db.add(
        AuthSession(
            id_hash=hash_token(token),
            user_id=user.id,
            expires_at=now() + dt.timedelta(days=days),
        )
    )
    return token


def user_for_session(db: Session, token: str | None) -> User | None:
    if not token or len(token) > 128:
        return None
    row = db.get(AuthSession, hash_token(token))
    if row is None:
        return None
    if _aware(row.expires_at) <= now():
        db.delete(row)
        db.commit()
        return None
    return db.get(User, row.user_id)


def end_session(db: Session, token: str | None) -> None:
    if token:
        db.execute(delete(AuthSession).where(AuthSession.id_hash == hash_token(token)))


def end_all_sessions(db: Session, user_id: str) -> None:
    db.execute(delete(AuthSession).where(AuthSession.user_id == user_id))


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


def issue_token(db: Session, user: User, purpose: str, lifetime: dt.timedelta) -> str:
    # One live token per purpose: asking again replaces the last link.
    db.execute(
        delete(AuthToken).where(AuthToken.user_id == user.id, AuthToken.purpose == purpose)
    )
    token = secrets.token_urlsafe(32)
    db.add(
        AuthToken(
            token_hash=hash_token(token),
            user_id=user.id,
            purpose=purpose,
            email=user.email,
            expires_at=now() + lifetime,
        )
    )
    return token


def redeem_token(db: Session, token: str, purpose: str) -> AuthToken:
    row = db.get(AuthToken, hash_token(token or ""))
    if (
        row is None
        or row.purpose != purpose
        or row.used_at is not None
        or _aware(row.expires_at) <= now()
    ):
        raise AuthError("that link has expired or was already used - ask for a new one")
    row.used_at = now()
    return row


# ---------------------------------------------------------------------------
# Email and password
# ---------------------------------------------------------------------------


def register(
    db: Session, email: str, password: str, display_name: str, *, accept_terms: bool = False
) -> User:
    email = normalize_email(email)
    display_name = (display_name or "").strip()[:64]
    if not EMAIL_RE.match(email):
        raise AuthError("enter a valid email address")
    if not display_name:
        raise AuthError("enter a display name")
    if not accept_terms:
        raise AuthError(
            "please confirm you're 16 or older and agree to the Terms and Privacy Policy"
        )
    trouble = passwords.problem(password, email=email, name=display_name)
    if trouble:
        raise AuthError(trouble)
    if db.scalar(select(User).where(User.email == email)) is not None:
        # Said plainly here, unlike login and reset: a registration form that
        # hides it cannot tell a person why they are not getting an account.
        # The rate limit on registering keeps this from being a lookup tool.
        raise AuthError("an account already uses that email - log in or reset the password")
    user = User(
        email=email,
        display_name=display_name,
        password_hash=passwords.hash_password(password),
        email_verified=False,
        terms_version=TERMS_VERSION,
        terms_accepted_at=now(),
    )
    db.add(user)
    db.flush()
    return user


def login(db: Session, email: str, password: str) -> User:
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    ok = passwords.verify(user.password_hash if user else None, password or "")
    if not ok or user is None:
        raise AuthError(GENERIC_LOGIN_ERROR, 401)
    if passwords.needs_rehash(user.password_hash):
        user.password_hash = passwords.hash_password(password)
    return user


def verify_email(db: Session, token: str) -> User:
    row = redeem_token(db, token, "verify")
    user = db.get(User, row.user_id)
    if user is None or user.email != row.email:
        raise AuthError("that link is for an address this account no longer uses")
    user.email_verified = True
    return user


def start_reset(db: Session, email: str, lifetime: dt.timedelta) -> tuple[User, str] | None:
    """A reset token for this address, or None - which the caller must not reveal."""
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None:
        return None
    return user, issue_token(db, user, "reset", lifetime)


def finish_reset(db: Session, token: str, password: str) -> User:
    row = redeem_token(db, token, "reset")
    user = db.get(User, row.user_id)
    if user is None:
        raise AuthError("that link has expired or was already used - ask for a new one")
    trouble = passwords.problem(password, email=user.email, name=user.display_name)
    if trouble:
        row.used_at = None  # a weak password does not burn the link
        raise AuthError(trouble)
    user.password_hash = passwords.hash_password(password)
    # Whoever knew the old password is signed out everywhere.
    end_all_sessions(db, user.id)
    # Clicking the link proves the address, too.
    if row.email == user.email:
        user.email_verified = True
    return user


def require_verified(user: User | None) -> User:
    """For saving decks and runs: a verified email, or a Discord login."""
    if user is None:
        raise AuthError("log in to save", 401)
    if not (user.email_verified or user.discord_id):
        raise AuthError("verify your email address to save - check your inbox", 403)
    return user


# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------


@dataclass
class DiscordResult:
    user: User
    created: bool
    linked: bool


def _apply_profile(user: User, profile: DiscordProfile) -> None:
    user.discord_id = profile.id
    user.discord_username = profile.username[:64] or None
    user.discord_global_name = (profile.global_name or "")[:64] or None
    user.discord_avatar = (profile.avatar or "")[:64] or None


def discord_login(
    db: Session, profile: DiscordProfile, *, link_to: User | None = None
) -> DiscordResult:
    """Sign in (or link) with a Discord profile.

    ``link_to`` is the signed-in user asking to link Discord from account
    settings. Otherwise: the account already holding this Discord id; else an
    account whose verified email Discord also reports as verified; else a
    new account.
    """
    owner = db.scalar(select(User).where(User.discord_id == profile.id))

    if link_to is not None:
        if owner is not None and owner.id != link_to.id:
            raise AuthError("that Discord account is already linked to another account", 409)
        _apply_profile(link_to, profile)
        return DiscordResult(link_to, created=False, linked=owner is None)

    if owner is not None:
        _apply_profile(owner, profile)  # names and avatars change
        return DiscordResult(owner, created=False, linked=False)

    if profile.email and profile.verified:
        match = db.scalar(select(User).where(User.email == profile.email))
        if match is not None and match.email_verified and match.discord_id is None:
            _apply_profile(match, profile)
            return DiscordResult(match, created=False, linked=True)

    email_free = (
        profile.email
        and profile.verified
        and db.scalar(select(User).where(User.email == profile.email)) is None
    )
    user = User(
        display_name=(profile.global_name or profile.username or "Discord user")[:64],
        email=profile.email if email_free else None,
        email_verified=bool(email_free),
        # The Discord button says that continuing accepts the Terms and the
        # Privacy Policy and confirms the person is 16 or older.
        terms_version=TERMS_VERSION,
        terms_accepted_at=now(),
    )
    _apply_profile(user, profile)
    db.add(user)
    db.flush()
    return DiscordResult(user, created=True, linked=False)


def unlink_discord(db: Session, user: User) -> User:
    if not user.discord_id:
        raise AuthError("no Discord account is linked")
    if not (user.password_hash and user.email):
        raise AuthError(
            "set an email and password first - unlinking Discord would leave "
            "no way to log in", 409,
        )
    user.discord_id = user.discord_username = None
    user.discord_global_name = user.discord_avatar = None
    return user


def delete_account(db: Session, user: User) -> None:
    """The user and everything of theirs. Sessions, tokens, verdicts (and,
    once they exist, decks and runs) go with it by cascading foreign keys."""
    db.delete(user)


def purge_expired(db: Session) -> dict:
    """Delete what the Privacy Policy says is not kept: expired sessions,
    expired or used tokens, and unverified email accounts past their grace."""
    moment = now()
    sessions = db.execute(delete(AuthSession).where(AuthSession.expires_at <= moment)).rowcount
    tokens = db.execute(
        delete(AuthToken).where(
            (AuthToken.expires_at <= moment)
            | (AuthToken.used_at.is_not(None) & (AuthToken.used_at <= moment - dt.timedelta(days=1)))
        )
    ).rowcount
    stale = db.scalars(
        select(User).where(
            User.email_verified.is_(False),
            User.discord_id.is_(None),
            User.created_at <= moment - dt.timedelta(days=UNVERIFIED_DAYS),
        )
    ).all()
    for user in stale:
        db.delete(user)
    db.commit()
    return {"sessions": sessions or 0, "tokens": tokens or 0, "unverified_users": len(stale)}


def export_data(db: Session, user: User) -> dict:
    """Everything stored about one person (UK GDPR Art. 15 and 20)."""
    import json as _json

    from ..models import UserVerdicts

    out: dict = {
        "exported_at": now().isoformat(),
        "account": user.public()
        | {
            "terms_version": user.terms_version,
            "terms_accepted_at": user.terms_accepted_at.isoformat()
            if user.terms_accepted_at else None,
            "has_password": bool(user.password_hash),
        },
        "sessions": [
            {"created_at": s.created_at.isoformat(), "expires_at": s.expires_at.isoformat()}
            for s in db.scalars(select(AuthSession).where(AuthSession.user_id == user.id))
        ],
    }
    verdicts = db.get(UserVerdicts, user.id)
    out["sandbox_verdicts"] = _json.loads(verdicts.data) if verdicts and verdicts.data else {}
    try:
        from ..models import Deck, DeckVersion, Run
    except ImportError:  # before saved decks existed
        return out
    decks = []
    for deck in db.scalars(select(Deck).where(Deck.user_id == user.id)):
        versions = db.scalars(
            select(DeckVersion).where(DeckVersion.deck_id == deck.id).order_by(DeckVersion.number)
        )
        decks.append({
            "name": deck.name,
            "commander": deck.commander,
            "created_at": deck.created_at.isoformat(),
            "versions": [
                {"number": v.number, "created_at": v.created_at.isoformat(), "decklist": v.decklist}
                for v in versions
            ],
        })
    out["decks"] = decks
    out["runs"] = [
        {
            "label": r.label,
            "created_at": r.created_at.isoformat(),
            "config": _json.loads(r.config),
            "report": _json.loads(r.report) if r.report else None,
        }
        for r in db.scalars(select(Run).where(Run.user_id == user.id))
    ]
    return out
