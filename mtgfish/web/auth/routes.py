"""The /auth/* routes.

=========================================  ===================================
``GET  /auth/me``                          the signed-in user, or 401
``POST /auth/register``                    email, password, display name
``POST /auth/login``                       email and password
``POST /auth/logout``                      end this browser's session
``POST /auth/verify``                      redeem an email-verification token
``POST /auth/verify/resend``               send the verification link again
``POST /auth/password/forgot``             email a reset link (never says if
                                           the address has an account)
``POST /auth/password/reset``              token + new password
``POST /auth/account``                     change the display name
``POST /auth/account/delete``              delete the account and its data
``GET  /auth/discord/login``               start Discord sign-in (``?link=1``
                                           to link it to this account)
``GET  /auth/discord/callback``            Discord sends the browser back here
``POST /auth/discord/unlink``              unlink Discord
``GET  /auth/verify``, ``/auth/reset``     email links land here and hand the
                                           token to the page
=========================================  ===================================

CSRF: every POST here must carry ``X-CSRF-Token`` equal to the
``mtgfish_csrf`` cookie (double submit), and the app refuses unsafe requests
whose ``Origin`` is another site. Rate limits apply to every route, with
tighter ones on register, login and reset.
"""

from __future__ import annotations

import datetime as dt
import logging
import secrets
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer

from ..models import User
from . import discord
from .ratelimit import RateLimited
from .service import AuthError

log = logging.getLogger("mtgfish.web.auth")

AUTH_COOKIE = "mtgfish_auth"
CSRF_COOKIE = "mtgfish_csrf"
STATE_COOKIE = "mtgfish_oauth"
STATE_MAX_AGE = 600


def _ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def build_router(auth) -> APIRouter:
    """``auth`` is the app's ``Accounts`` (database, settings, mailer, limits)."""
    from . import service

    router = APIRouter(prefix="/auth")
    settings = auth.settings
    signer = URLSafeTimedSerializer(settings.session_secret, salt="mtgfish-oauth-state")

    # -- helpers ------------------------------------------------------------

    def secure(request: Request) -> bool:
        return settings.secure_cookies or request.headers.get("x-forwarded-proto") == "https"

    def reply(payload: dict, status: int = 200) -> JSONResponse:
        return JSONResponse(payload, status_code=status, headers={"Cache-Control": "no-store"})

    def refuse(exc: AuthError | RateLimited) -> JSONResponse:
        if isinstance(exc, RateLimited):
            return JSONResponse(
                {"error": str(exc)}, status_code=429,
                headers={"Retry-After": str(exc.retry_after), "Cache-Control": "no-store"},
            )
        return reply({"error": str(exc)}, exc.status)

    def sign_in(response: Response, request: Request, db, user: User) -> None:
        token = service.create_session(db, user, settings.session_days)
        response.set_cookie(
            AUTH_COOKIE, token, max_age=settings.session_days * 86400, path="/",
            httponly=True, samesite="lax", secure=secure(request),
        )

    def sign_out(response: Response) -> None:
        response.delete_cookie(AUTH_COOKIE, path="/")

    def check(request: Request, *names_keys: tuple[str, str]) -> None:
        """Rate limits, then CSRF, for a POST."""
        auth.limits.hit("auth", _ip(request))
        for name, key in names_keys:
            auth.limits.hit(name, key)
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

    def link(path: str, token: str) -> str:
        return f"{settings.public_base_url}{path}?token={quote(token)}"

    def send_verification(db, user: User) -> None:
        token = service.issue_token(
            db, user, "verify", dt.timedelta(hours=settings.verify_hours)
        )
        db.commit()
        auth.mailer().send(
            user.email,
            "Confirm your Goldfisher email",
            f"Hi {user.display_name},\n\nConfirm this address for your Goldfisher account:\n"
            f"{link('/auth/verify', token)}\n\nThe link works once and expires in "
            f"{settings.verify_hours} hours. If you did not sign up, ignore this email.\n",
        )

    # -- reading ------------------------------------------------------------

    @router.get("/me")
    def me(request: Request):
        csrf = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(24)
        with auth.db.session() as db:
            user = service.user_for_session(db, request.cookies.get(AUTH_COOKIE))
            payload = {
                "user": user.public() if user else None,
                "csrf": csrf,
                "discord": {"enabled": settings.discord_enabled, "fake": settings.discord_fake},
            }
        response = reply(payload, 200 if user else 401)
        if request.cookies.get(CSRF_COOKIE) != csrf:
            # Readable by the page's script (not HttpOnly) - that is the point
            # of a double-submit token; another site cannot read it.
            response.set_cookie(
                CSRF_COOKIE, csrf, max_age=30 * 86400, path="/", httponly=False,
                samesite="lax", secure=secure(request),
            )
        return response

    # -- email and password -------------------------------------------------

    @router.post("/register")
    async def register(request: Request):
        data = await body(request)
        try:
            check(request, ("register", _ip(request)))
            with auth.db.session() as db:
                user = service.register(
                    db, str(data.get("email", "")), str(data.get("password", "")),
                    str(data.get("display_name", "")),
                )
                db.commit()
                try:
                    send_verification(db, user)
                except Exception:  # noqa: BLE001 - the account exists either way
                    log.exception("could not send the verification email")
                response = reply({"user": user.public(), "verification_sent": True}, 201)
                sign_in(response, request, db, user)
                db.commit()
                return response
        except (AuthError, RateLimited) as exc:
            return refuse(exc)

    @router.post("/login")
    async def login(request: Request):
        data = await body(request)
        email = service.normalize_email(str(data.get("email", "")))
        try:
            check(request, ("login", _ip(request)), ("login_email", email))
            with auth.db.session() as db:
                user = service.login(db, email, str(data.get("password", "")))
                response = reply({"user": user.public()})
                sign_in(response, request, db, user)
                db.commit()
                return response
        except (AuthError, RateLimited) as exc:
            return refuse(exc)

    @router.post("/logout")
    async def logout(request: Request):
        try:
            check(request)
        except (AuthError, RateLimited) as exc:
            return refuse(exc)
        with auth.db.session() as db:
            service.end_session(db, request.cookies.get(AUTH_COOKIE))
            db.commit()
        response = reply({"ok": True})
        sign_out(response)
        return response

    @router.post("/verify")
    async def verify(request: Request):
        data = await body(request)
        try:
            check(request)
            with auth.db.session() as db:
                user = service.verify_email(db, str(data.get("token", "")))
                db.commit()
                return reply({"user": user.public()})
        except (AuthError, RateLimited) as exc:
            return refuse(exc)

    @router.post("/verify/resend")
    async def verify_resend(request: Request):
        try:
            with auth.db.session() as db:
                user = service.user_for_session(db, request.cookies.get(AUTH_COOKIE))
                if user is None:
                    raise AuthError("log in first", 401)
                check(request, ("verify_resend", user.id))
                if user.email_verified or not user.email:
                    return reply({"ok": True, "already": True})
                send_verification(db, user)
                return reply({"ok": True})
        except (AuthError, RateLimited) as exc:
            return refuse(exc)

    @router.post("/password/forgot")
    async def forgot(request: Request):
        data = await body(request)
        email = service.normalize_email(str(data.get("email", "")))
        generic = {"ok": True, "message": "if that address has an account, we've sent a link"}
        try:
            check(request, ("reset", _ip(request)), ("reset_email", email))
        except (AuthError, RateLimited) as exc:
            return refuse(exc)
        with auth.db.session() as db:
            found = service.start_reset(db, email, dt.timedelta(minutes=settings.reset_minutes))
            db.commit()
            if found is not None:
                user, token = found
                try:
                    auth.mailer().send(
                        user.email,
                        "Reset your Goldfisher password",
                        f"Hi {user.display_name},\n\nSet a new password here:\n"
                        f"{link('/auth/reset', token)}\n\nThe link works once and expires "
                        f"in {settings.reset_minutes} minutes. If you did not ask for this, "
                        "ignore this email; your password is unchanged.\n",
                    )
                except Exception:  # noqa: BLE001 - never reveal it through an error
                    log.exception("could not send a reset email")
        return reply(generic)

    @router.post("/password/reset")
    async def reset(request: Request):
        data = await body(request)
        try:
            check(request, ("reset", _ip(request)))
            with auth.db.session() as db:
                user = service.finish_reset(
                    db, str(data.get("token", "")), str(data.get("password", ""))
                )
                db.commit()
                response = reply({"user": user.public()})
                sign_in(response, request, db, user)
                db.commit()
                return response
        except (AuthError, RateLimited) as exc:
            return refuse(exc)

    # -- account ------------------------------------------------------------

    @router.post("/account")
    async def update_account(request: Request):
        data = await body(request)
        try:
            check(request)
            with auth.db.session() as db:
                user = service.user_for_session(db, request.cookies.get(AUTH_COOKIE))
                if user is None:
                    raise AuthError("log in first", 401)
                name = str(data.get("display_name", "")).strip()[:64]
                if not name:
                    raise AuthError("enter a display name")
                user.display_name = name
                db.commit()
                return reply({"user": user.public()})
        except (AuthError, RateLimited) as exc:
            return refuse(exc)

    @router.post("/account/delete")
    async def delete_account(request: Request):
        data = await body(request)
        try:
            check(request)
            with auth.db.session() as db:
                user = service.user_for_session(db, request.cookies.get(AUTH_COOKIE))
                if user is None:
                    raise AuthError("log in first", 401)
                if str(data.get("confirm", "")) != "DELETE":
                    raise AuthError('type DELETE to confirm')
                user_id = user.id
                service.delete_account(db, user)
                db.commit()
            auth.forget_user(user_id)
            response = reply({"deleted": True})
            sign_out(response)
            return response
        except (AuthError, RateLimited) as exc:
            return refuse(exc)

    # -- email links --------------------------------------------------------

    @router.get("/verify")
    def verify_link(token: str = ""):
        # Handed to the page rather than redeemed here: mail scanners fetch
        # links, and a GET that used the token up would verify on their visit.
        return RedirectResponse(f"/#verify={quote(token)}", status_code=303)

    @router.get("/reset")
    def reset_link(token: str = ""):
        return RedirectResponse(f"/#reset={quote(token)}", status_code=303)

    # -- Discord ------------------------------------------------------------

    @router.get("/discord/login")
    def discord_login(request: Request, link: int = 0):
        try:
            auth.limits.hit("oauth", _ip(request))
        except RateLimited as exc:
            return refuse(exc)
        if not settings.discord_enabled and not settings.discord_fake:
            return reply({"error": "Discord login is not configured on this server"}, 503)

        state, verifier = discord.new_state(), discord.new_verifier()
        intent = {"state": state, "verifier": verifier, "link": bool(link)}
        if settings.discord_fake:
            target = f"/auth/discord/callback?code=dev&state={state}"
        else:
            target = discord.authorize_url(
                settings.discord_client_id, settings.discord_redirect_uri, state, verifier
            )
        response = RedirectResponse(target, status_code=303)
        response.set_cookie(
            STATE_COOKIE, signer.dumps(intent), max_age=STATE_MAX_AGE, path="/auth/discord",
            httponly=True, samesite="lax", secure=secure(request),
        )
        return response

    @router.get("/discord/callback")
    def discord_callback(request: Request, code: str = "", state: str = "", error: str = ""):
        def home(message: str = "") -> RedirectResponse:
            target = "/" + (f"#auth_error={quote(message)}" if message else "")
            response = RedirectResponse(target, status_code=303)
            response.delete_cookie(STATE_COOKIE, path="/auth/discord")
            return response

        try:
            auth.limits.hit("oauth", _ip(request))
        except RateLimited as exc:
            return refuse(exc)
        try:
            intent = signer.loads(request.cookies.get(STATE_COOKIE, ""), max_age=STATE_MAX_AGE)
        except BadSignature:
            return home("the Discord sign-in expired - try again")
        if not state or not secrets.compare_digest(str(intent.get("state", "")), state):
            return home("the Discord sign-in did not match this browser - try again")
        if error or not code:
            return home("Discord sign-in was cancelled")

        try:
            if settings.discord_fake:
                if not code == "dev":
                    return home("Discord sign-in failed")
                profile = discord.DiscordProfile(
                    id="dev-0000000001", username="dev_user", global_name="Dev Discord User",
                    avatar=None, email="dev-discord@localhost.test", verified=True,
                )
            else:
                token = discord.exchange(
                    code,
                    client_id=settings.discord_client_id,
                    client_secret=settings.discord_client_secret,
                    redirect_uri=settings.discord_redirect_uri,
                    verifier=str(intent.get("verifier", "")),
                    transport=auth.http_transport,
                )
                profile = discord.profile(token, transport=auth.http_transport)
                del token  # used for the profile, never kept
        except discord.DiscordError as exc:
            log.warning("Discord sign-in failed: %s", exc)
            return home(str(exc))

        with auth.db.session() as db:
            current = service.user_for_session(db, request.cookies.get(AUTH_COOKIE))
            link_to = current if intent.get("link") else None
            if intent.get("link") and current is None:
                return home("log in first, then link Discord from your account settings")
            try:
                result = service.discord_login(db, profile, link_to=link_to)
            except AuthError as exc:
                return home(str(exc))
            db.commit()
            response = home()
            if link_to is None:
                sign_in(response, request, db, result.user)
                db.commit()
            return response

    @router.post("/discord/unlink")
    async def discord_unlink(request: Request):
        try:
            check(request)
            with auth.db.session() as db:
                user = service.user_for_session(db, request.cookies.get(AUTH_COOKIE))
                if user is None:
                    raise AuthError("log in first", 401)
                service.unlink_discord(db, user)
                db.commit()
                return reply({"user": user.public()})
        except (AuthError, RateLimited) as exc:
            return refuse(exc)

    return router
