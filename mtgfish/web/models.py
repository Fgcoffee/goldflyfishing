"""Tables for accounts. The schema itself is in the Alembic migrations;
these classes are how the code reads and writes it, and a test checks the two
agree."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def new_id() -> str:
    return uuid.uuid4().hex


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    display_name: Mapped[str] = mapped_column(String(64))
    #: Lower-cased. Null for an account that only ever signed in with Discord
    #: and whose Discord email was not verified.
    email: Mapped[str | None] = mapped_column(String(254), unique=True, nullable=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    #: argon2id. Null for a Discord-only account.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    discord_id: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    discord_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    discord_global_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    discord_avatar: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)
    #: The version of the Terms and Privacy Policy accepted, and when.
    terms_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    terms_accepted_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    sessions: Mapped[list[AuthSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def avatar_url(self) -> str | None:
        if self.discord_id and self.discord_avatar:
            return (
                f"https://cdn.discordapp.com/avatars/{self.discord_id}/"
                f"{self.discord_avatar}.png?size=64"
            )
        return None

    @property
    def login_methods(self) -> list[str]:
        methods = []
        if self.password_hash and self.email:
            methods.append("password")
        if self.discord_id:
            methods.append("discord")
        return methods

    def public(self) -> dict:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "email": self.email,
            "email_verified": self.email_verified,
            "discord": (
                {
                    "id": self.discord_id,
                    "username": self.discord_username,
                    "global_name": self.discord_global_name,
                }
                if self.discord_id
                else None
            ),
            "avatar_url": self.avatar_url,
            "login_methods": self.login_methods,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class AuthSession(Base):
    """A signed-in browser. The cookie holds a random id; only its hash is
    stored, so a leaked database does not hand out live sessions."""

    __tablename__ = "sessions"

    id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="sessions")


class AuthToken(Base):
    """A single-use emailed token (verify an address, reset a password).
    Stored as a hash; the plain token exists only in the email."""

    __tablename__ = "auth_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    purpose: Mapped[str] = mapped_column(String(16))  # "verify" | "reset"
    #: The address a verify token was sent to: verifying it proves *that*
    #: address, which matters if the account's email changed since.
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


Index("ix_auth_tokens_user_purpose", AuthToken.user_id, AuthToken.purpose)


class UserVerdicts(Base):
    """One account's sandbox verdicts ("this card reads wrong - switch it
    off"), as the same JSON the desktop app keeps in a file."""

    __tablename__ = "user_verdicts"

    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    data: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)


class Deck(Base):
    """A saved deck. Its lists are versions: saving a changed list adds one,
    so a run can always point at the exact list it played."""

    __tablename__ = "decks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    commander: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)

    versions: Mapped[list[DeckVersion]] = relationship(
        back_populates="deck", cascade="all, delete-orphan", passive_deletes=True
    )


class DeckVersion(Base):
    __tablename__ = "deck_versions"
    __table_args__ = (UniqueConstraint("deck_id", "number", name="uq_deck_versions_number"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    deck_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("decks.id", ondelete="CASCADE"), index=True
    )
    number: Mapped[int] = mapped_column(Integer)
    decklist: Mapped[str] = mapped_column(Text)
    #: Of the list with blank lines and trailing spaces ignored: how a run's
    #: decklist is matched back to the version it played.
    text_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)

    deck: Mapped[Deck] = relationship(back_populates="versions")


class Run(Base):
    """One simulation in someone's history: the full ``RunConfig`` (enough to
    replay any of its games from the seed) and the report it produced."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    deck_version_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("deck_versions.id", ondelete="SET NULL"), nullable=True
    )
    label: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), default="done")
    #: JSON: decklists, games, max_rounds, run_seed, hero, card_db_hash.
    config: Mapped[str] = mapped_column(Text)
    #: JSON: the report the Results screen draws (win rate, stall-outs, ending
    #: reasons, unread cards, card impact, ...).
    report: Mapped[str | None] = mapped_column(Text, nullable=True)
    win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    games: Mapped[int] = mapped_column(Integer)
    run_seed: Mapped[int] = mapped_column(Integer)
    card_db_hash: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
