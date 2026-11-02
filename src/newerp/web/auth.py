"""Login, logout and own password (Login.razor, ChangePassword.razor). The same cookie as POST /auth/login."""

import hmac
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from newerp.api.deps import get_auth_service, get_login_throttle
from newerp.api.security import sign_in, sign_out
from newerp.application.auth.models import ChangePasswordRequest, LoginRequest
from newerp.application.auth.services import AuthService
from newerp.application.auth.throttle import LoginThrottle, whole_minutes
from newerp.config import get_settings
from newerp.domain.common import DomainError
from newerp.web.common import WebUser, local_path, render

router = APIRouter(prefix="/app", include_in_schema=False)
Service = Annotated[AuthService, Depends(get_auth_service)]
Field = Annotated[str, Form()]


# Login CSRF: SameSite=Strict protects the session cookie, but before the login there is no session, so a form on
# another site could log this browser into the attacker's account. The login page sets a random token in a
# SameSite=Strict cookie and in the form; a POST counts only when both are there and equal (double submit).
CSRF_COOKIE = "newerp.login_csrf"


def _login_page(request: Request, status_code: int = 200, **context: str | None) -> HTMLResponse:
    token = secrets.token_urlsafe(32)
    response = render(request, "login.html", status_code=status_code, csrf=token, **context)
    response.set_cookie(
        CSRF_COOKIE, token, path="/app/login", httponly=True, samesite="strict",
        secure=not get_settings().is_development,
    )  # fmt: skip
    return response


def _same_token(request: Request, sent: str) -> bool:
    kept = request.cookies.get(CSRF_COOKIE, "")
    return bool(kept) and bool(sent) and hmac.compare_digest(kept.encode(), sent.encode())


@router.get("/login")
async def login_page(request: Request, next: str | None = None) -> HTMLResponse:
    return _login_page(request, next=local_path(next))


@router.post("/login")
async def login(
    request: Request,
    service: Service,
    throttle: Annotated[LoginThrottle, Depends(get_login_throttle)],
    username: Field = "",
    password: Field = "",
    next: Field = "",
    csrf: Field = "",
) -> Response:
    page = {"next": local_path(next), "username": username}
    if not _same_token(request, csrf):
        error = "Сторінку входу відкрито з іншого сайту або вона застаріла. Спробуйте ще раз."
        return _login_page(request, status_code=400, error=error, **page)
    address = request.client.host if request.client else None
    wait = await throttle.retry_after(address, username)
    if wait is not None:
        error = f"Забагато невдалих спроб входу. Спробуйте знову через {whole_minutes(wait)} хв."
        return _login_page(request, status_code=429, error=error, **page)

    authenticated = await service.login(LoginRequest(username=username, password=password))
    if authenticated is None:
        await throttle.failed(address, username)
        return _login_page(request, status_code=401, error="Невірний логін або пароль", **page)

    await throttle.succeeded(username)
    sign_in(request, authenticated)
    target = "/app/password" if authenticated.user.must_change_password else page["next"]
    return RedirectResponse(target, status_code=303)  # 303: the browser follows with GET, so F5 does not resend


# A POST form, not a link: the SameSite=Strict cookie is not sent with a form from another site (no CSRF).
@router.post("/logout")
async def logout(request: Request) -> Response:
    sign_out(request)
    return RedirectResponse("/app/login", status_code=303)


@router.get("/password")
async def password_page(request: Request, _: WebUser) -> HTMLResponse:
    return render(request, "password.html")


@router.post("/password")
async def change_password(
    request: Request,
    user: WebUser,
    service: Service,
    current_password: Field = "",
    new_password: Field = "",
    confirm_password: Field = "",
) -> Response:
    if new_password != confirm_password:
        return render(request, "password.html", status_code=400, error="Новий пароль і підтвердження не збігаються.")
    try:
        authenticated = await service.change_password(
            user.user_id, ChangePasswordRequest(current_password=current_password, new_password=new_password)
        )
    except DomainError as error:
        return render(request, "password.html", status_code=400, error=str(error))
    sign_in(request, authenticated)  # the new security stamp closed every session, this one is issued again
    return RedirectResponse("/app/", status_code=303)
