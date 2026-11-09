"""Discord sign-in: the OAuth2 authorization-code flow, with PKCE.

Small enough to own outright, so no auth library or service is involved:

1. ``authorize_url`` sends the browser to Discord with ``response_type=code``,
   our client id and redirect URI, ``scope=identify email``, a random
   ``state`` and a PKCE ``code_challenge`` (S256). The state and the PKCE
   verifier go into a signed, ten-minute cookie on this browser.
2. Discord sends the browser back to ``/auth/discord/callback`` with ``code``
   and ``state``. The state must match the cookie's, or nothing happens.
3. ``exchange`` trades the code (plus the verifier) for an access token at
   ``POST /api/oauth2/token``, and ``profile`` reads ``GET /api/users/@me``.

Discord's tokens are used for those two calls and then dropped; nothing of
Discord's beyond the profile is stored.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

AUTHORIZE = "https://discord.com/oauth2/authorize"
TOKEN = "https://discord.com/api/oauth2/token"
ME = "https://discord.com/api/users/@me"
SCOPES = "identify email"


class DiscordError(RuntimeError):
    pass


@dataclass
class DiscordProfile:
    id: str
    username: str
    global_name: str | None
    avatar: str | None
    email: str | None
    verified: bool

    @classmethod
    def from_api(cls, data: dict) -> DiscordProfile:
        if not data.get("id"):
            raise DiscordError("Discord did not say who you are")
        email = data.get("email")
        return cls(
            id=str(data["id"]),
            username=str(data.get("username") or ""),
            global_name=data.get("global_name"),
            avatar=data.get("avatar"),
            email=email.strip().lower() if isinstance(email, str) and email.strip() else None,
            verified=bool(data.get("verified")),
        )


def new_state() -> str:
    return secrets.token_urlsafe(24)


def new_verifier() -> str:
    # RFC 7636: 43-128 characters from the unreserved set.
    return secrets.token_urlsafe(64)[:96]


def challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorize_url(client_id: str, redirect_uri: str, state: str, verifier: str) -> str:
    return AUTHORIZE + "?" + urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": SCOPES,
            "state": state,
            "code_challenge": challenge(verifier),
            "code_challenge_method": "S256",
            "prompt": "none",
        }
    )


def exchange(
    code: str,
    *,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    verifier: str,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """The authorization code for an access token."""
    with httpx.Client(transport=transport, timeout=15) as client:
        reply = client.post(
            TOKEN,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            },
            auth=(client_id, client_secret),
            headers={"Accept": "application/json"},
        )
    if reply.status_code != 200:
        raise DiscordError(f"Discord refused the sign-in ({reply.status_code})")
    token = reply.json().get("access_token")
    if not token:
        raise DiscordError("Discord sent no access token")
    return token


def profile(access_token: str, *, transport: httpx.BaseTransport | None = None) -> DiscordProfile:
    with httpx.Client(transport=transport, timeout=15) as client:
        reply = client.get(ME, headers={"Authorization": f"Bearer {access_token}"})
    if reply.status_code != 200:
        raise DiscordError(f"Discord would not share your profile ({reply.status_code})")
    return DiscordProfile.from_api(reply.json())
