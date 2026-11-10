"""Accounts for the web app: email + password and Discord, built in FastAPI.

``Accounts`` holds what the auth routes need - the database, settings, the
mailer and the rate limiter - and is how the rest of the app asks "who is
this request?".
"""

from __future__ import annotations

from .config import AuthSettings, ConfigError
from .mail import LogMailer, Mailer, MailNotConfigured, mailer_from_env
from .ratelimit import RateLimiter

__all__ = ["Accounts", "AuthSettings", "ConfigError"]


class Accounts:
    def __init__(
        self,
        database,
        settings: AuthSettings | None = None,
        *,
        mailer: Mailer | None = None,
        limits: RateLimiter | None = None,
        http_transport=None,
    ) -> None:
        self.db = database
        self.settings = settings or AuthSettings()
        self._mailer = mailer
        self.limits = limits or RateLimiter()
        #: An httpx transport for Discord's API; tests pass a MockTransport.
        self.http_transport = http_transport
        #: Called with a user id when that account is deleted, so live
        #: browser sessions stop acting as it.
        self.on_forget = None
        #: Whether email + password accounts are offered. In production that
        #: needs a way to send email (verification, resets); without one the
        #: site runs Discord-only rather than refusing to start.
        from .mail import mail_configured

        self.email_enabled = (
            mailer is not None or mail_configured() or not self.settings.production
        )

    def mailer(self) -> Mailer:
        if self._mailer is None:
            self._mailer = mailer_from_env(production=self.settings.production)
        return self._mailer

    def user_for(self, token: str | None):
        """The signed-in user for an auth cookie value, or None."""
        from .service import user_for_session

        if not token:
            return None
        with self.db.session() as db:
            return user_for_session(db, token)

    def forget_user(self, user_id: str) -> None:
        if self.on_forget is not None:
            self.on_forget(user_id)


# Re-exported for tests and callers.
LogMailer = LogMailer
MailNotConfigured = MailNotConfigured
