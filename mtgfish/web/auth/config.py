"""Account settings, from the environment. Secrets are never committed.

=====================  ==================================================
``MTGFISH_ENV``        ``production`` turns on the strict checks below
``SESSION_SECRET``     signs short-lived cookies (OAuth state). Required in
                       production; a random one per process otherwise
``DATABASE_URL``       see ``web.db``
``PUBLIC_BASE_URL``    where the site lives; redirect URIs are built from it
``DISCORD_CLIENT_ID``  the Discord application's id (public)
``DISCORD_CLIENT_SECRET``  its secret. Unset: Discord login is a local fake
                       in development and refused in production
``SMTP_*``, ``RESEND_API_KEY``, ``MAIL_FROM``  see ``auth.mail``
=====================  ==================================================
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field


class ConfigError(RuntimeError):
    pass


def _bool(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() not in ("", "0", "false", "no", "off")


@dataclass
class AuthSettings:
    production: bool = field(
        default_factory=lambda: os.environ.get("MTGFISH_ENV", "").lower() == "production"
    )
    session_secret: str = field(default_factory=lambda: os.environ.get("SESSION_SECRET", ""))
    public_base_url: str = field(
        default_factory=lambda: os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    )
    discord_client_id: str = field(default_factory=lambda: os.environ.get("DISCORD_CLIENT_ID", ""))
    discord_client_secret: str = field(
        default_factory=lambda: os.environ.get("DISCORD_CLIENT_SECRET", "")
    )
    secure_cookies: bool = field(default_factory=lambda: _bool("MTGFISH_SECURE_COOKIES"))
    #: Days a sign-in lasts.
    session_days: int = 30
    #: Minutes a password-reset link stays good.
    reset_minutes: int = 30
    #: Hours an email-verification link stays good.
    verify_hours: int = 48

    def __post_init__(self) -> None:
        if self.production:
            if len(self.session_secret) < 32:
                raise ConfigError("SESSION_SECRET (32+ random characters) is required in production")
            if not self.public_base_url.startswith("https://"):
                raise ConfigError("PUBLIC_BASE_URL must be https:// in production")
            self.secure_cookies = True
        if not self.session_secret:
            # Development: a fresh secret per process. Signed state cookies
            # from before a restart stop verifying, which is harmless.
            self.session_secret = secrets.token_urlsafe(48)

    @property
    def discord_redirect_uri(self) -> str:
        return f"{self.public_base_url}/auth/discord/callback"

    @property
    def discord_enabled(self) -> bool:
        return bool(self.discord_client_id and self.discord_client_secret)

    @property
    def discord_fake(self) -> bool:
        """The local stand-in for Discord, for development without a secret."""
        return not self.discord_enabled and not self.production
