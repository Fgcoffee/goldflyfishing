"""Sending email: verification links and password resets.

One small interface, three ways to deliver:

* ``SmtpMailer`` when ``SMTP_HOST`` is set (``SMTP_PORT``, ``SMTP_USER``,
  ``SMTP_PASSWORD``; STARTTLS on 587, TLS on 465);
* ``ResendMailer`` when ``RESEND_API_KEY`` is set (https://resend.com);
* ``LogMailer`` otherwise - development only. It writes the email, link and
  all, to the log, which is the point locally and a leak in production, so
  production refuses to use it.

``MAIL_FROM`` is the sender, e.g. ``Goldfisher <no-reply@example.com>``.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Protocol

log = logging.getLogger("mtgfish.web.mail")


class MailNotConfigured(RuntimeError):
    pass


class Mailer(Protocol):
    def send(self, to: str, subject: str, text: str) -> None: ...


@dataclass
class LogMailer:
    """Development: emails go to the log (and are kept, for tests)."""

    sent: list[tuple[str, str, str]] = field(default_factory=list)

    def send(self, to: str, subject: str, text: str) -> None:
        self.sent.append((to, subject, text))
        log.warning("DEV MAIL to %s: %s\n%s", to, subject, text)


@dataclass
class SmtpMailer:
    host: str
    port: int
    user: str
    password: str
    sender: str

    def send(self, to: str, subject: str, text: str) -> None:
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = to
        message["Subject"] = subject
        message.set_content(text)
        context = ssl.create_default_context()
        if self.port == 465:
            with smtplib.SMTP_SSL(self.host, self.port, context=context, timeout=20) as smtp:
                self._deliver(smtp, message)
        else:
            with smtplib.SMTP(self.host, self.port, timeout=20) as smtp:
                smtp.starttls(context=context)
                self._deliver(smtp, message)

    def _deliver(self, smtp, message) -> None:
        if self.user:
            smtp.login(self.user, self.password)
        smtp.send_message(message)


@dataclass
class ResendMailer:
    api_key: str
    sender: str
    transport: object = None  # an httpx transport, for tests

    def send(self, to: str, subject: str, text: str) -> None:
        import httpx

        with httpx.Client(transport=self.transport, timeout=20) as client:
            reply = client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"from": self.sender, "to": [to], "subject": subject, "text": text},
            )
        if reply.status_code >= 300:
            raise RuntimeError(f"Resend answered {reply.status_code}")


def mail_configured() -> bool:
    return bool(os.environ.get("SMTP_HOST") or os.environ.get("RESEND_API_KEY"))


def mailer_from_env(*, production: bool) -> Mailer:
    sender = os.environ.get("MAIL_FROM", "Goldfisher <no-reply@localhost>")
    if os.environ.get("SMTP_HOST"):
        return SmtpMailer(
            host=os.environ["SMTP_HOST"],
            port=int(os.environ.get("SMTP_PORT", "587")),
            user=os.environ.get("SMTP_USER", ""),
            password=os.environ.get("SMTP_PASSWORD", ""),
            sender=sender,
        )
    if os.environ.get("RESEND_API_KEY"):
        return ResendMailer(api_key=os.environ["RESEND_API_KEY"], sender=sender)
    if production:
        raise MailNotConfigured(
            "set SMTP_HOST (or RESEND_API_KEY) and MAIL_FROM to send account emails"
        )
    return LogMailer()
