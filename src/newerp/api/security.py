"""Cookie session and role checks (like AuthSetup.cs + UserClaims.cs in .NET).

After POST /auth/login the server sets a signed cookie, and the browser sends it with every request. On each
request the security stamp in the cookie is compared with erp_user_login: after a role or password change, or
when access is revoked or the user is disabled on the old site, the old cookie stops working (401).
"""

import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Annotated

from fastapi import Depends, FastAPI, Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer

from newerp.api.deps import UserLoginStoreDep
from newerp.api.errors import Forbidden, NotAuthenticated
from newerp.application.auth.models import AuthenticatedUser, CurrentUser
from newerp.application.auth.policy import Roles
from newerp.config import get_settings

COOKIE_NAME = "newerp.auth"
LIFETIME_SECONDS = 8 * 60 * 60
_CLEAR = "clear"


@dataclass(frozen=True)
class Principal:
    """What the cookie holds (the claims in .NET)."""

    user_id: int
    username: str
    full_name: str
    role: str
    security_stamp: str
    must_change_password: bool

    @staticmethod
    def of(authenticated: AuthenticatedUser) -> "Principal":
        user = authenticated.user
        return Principal(
            user.user_id,
            user.username,
            user.full_name,
            user.role,
            authenticated.security_stamp,
            user.must_change_password,
        )

    def to_current_user(self) -> CurrentUser:
        return CurrentUser(
            user_id=self.user_id,
            username=self.username,
            full_name=self.full_name,
            role=self.role,
            must_change_password=self.must_change_password,
        )


def _serializer() -> URLSafeTimedSerializer:
    settings = get_settings()
    secret = settings.auth_secret.get_secret_value() if settings.auth_secret else _FALLBACK_SECRET
    return URLSafeTimedSerializer(secret, salt=COOKIE_NAME)


_FALLBACK_SECRET = secrets.token_urlsafe(32)


def sign_in(request: Request, authenticated: AuthenticatedUser) -> None:
    """The cookie is written by the middleware below, after the endpoint has run."""
    request.state.auth_cookie = Principal.of(authenticated)


def sign_out(request: Request) -> None:
    request.state.auth_cookie = _CLEAR


def read_principal(request: Request) -> tuple[Principal, float] | None:
    """The principal from a valid, unexpired cookie and the time it was issued; None otherwise."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        data, issued = _serializer().loads(token, max_age=LIFETIME_SECONDS, return_timestamp=True)
        return Principal(**data), issued.timestamp()
    except (BadSignature, TypeError):
        return None


async def current_principal(request: Request, store: UserLoginStoreDep) -> Principal:
    found = read_principal(request)
    if found is None:
        if COOKIE_NAME in request.cookies:
            sign_out(request)
        raise NotAuthenticated
    principal, issued = found

    if await store.get_active_security_stamp(principal.user_id) != principal.security_stamp:
        sign_out(request)
        raise NotAuthenticated

    # Sliding expiration: while the user works, the session is extended (renewed after half of its lifetime).
    if time.time() - issued > LIFETIME_SECONDS / 2 and not hasattr(request.state, "auth_cookie"):
        request.state.auth_cookie = principal
    return principal


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


def require_roles(*roles: str) -> Callable[[Principal], Awaitable[Principal]]:
    """Router dependency: only these roles may call the endpoints (the policies in .NET)."""

    async def check(principal: CurrentPrincipal) -> Principal:
        if principal.role not in roles:
            raise Forbidden
        return principal

    return check


class Policies:
    """Which roles may use which module."""

    ORDERS = (Roles.ADMIN, Roles.MANAGER)
    STOCK = (Roles.ADMIN, Roles.STOREKEEPER)
    BONUSES = (Roles.ADMIN,)
    USERS = (Roles.ADMIN,)
    # Cashbox and bank: receipts and shifts for managers, cashier setup for the admin only.
    FINANCE = (Roles.ADMIN, Roles.MANAGER)
    FINANCE_SETUP = (Roles.ADMIN,)


# Every response gets these headers (SecurityHeaders.cs).
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https:; font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; "
    "form-action 'self'; frame-ancestors 'none'"
)


def add_security_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def security(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)

        cookie = getattr(request.state, "auth_cookie", None)
        if cookie == _CLEAR:
            response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="strict")
        elif isinstance(cookie, Principal):
            response.set_cookie(
                COOKIE_NAME,
                _serializer().dumps(asdict(cookie)),
                max_age=LIFETIME_SECONDS,
                path="/",
                httponly=True,  # JavaScript on the page cannot read the cookie
                samesite="strict",  # other sites cannot send requests on the user's behalf (CSRF)
                secure=not get_settings().is_development,  # on a server the cookie travels over HTTPS only
            )

        headers = response.headers
        headers["X-Content-Type-Options"] = "nosniff"
        headers["X-Frame-Options"] = "DENY"
        headers["Referrer-Policy"] = "no-referrer"
        headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        headers["Cross-Origin-Opener-Policy"] = "same-origin"
        # The interactive docs (/docs) load their own scripts, so they get no content policy.
        if not request.url.path.startswith(("/docs", "/redoc", "/openapi.json")):
            headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        return response
