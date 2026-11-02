"""Login, logout and own password (Login.razor, ChangePassword.razor). The same cookie as POST /auth/login."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from newerp.api.deps import get_auth_service, get_login_throttle
from newerp.api.security import sign_in, sign_out
from newerp.application.auth.models import ChangePasswordRequest, LoginRequest
from newerp.application.auth.services import AuthService
from newerp.application.auth.throttle import LoginThrottle, whole_minutes
from newerp.domain.common import DomainError
from newerp.web.common import WebUser, local_path, render

router = APIRouter(prefix="/app", include_in_schema=False)
Service = Annotated[AuthService, Depends(get_auth_service)]
Field = Annotated[str, Form()]


@router.get("/login")
async def login_page(request: Request, next: str | None = None) -> HTMLResponse:
    return render(request, "login.html", next=local_path(next))


@router.post("/login")
async def login(
    request: Request,
    service: Service,
    throttle: Annotated[LoginThrottle, Depends(get_login_throttle)],
    username: Field = "",
    password: Field = "",
    next: Field = "",
) -> Response:
    page = {"next": local_path(next), "username": username}
    address = request.client.host if request.client else None
    wait = throttle.retry_after(address, username)
    if wait is not None:
        error = f"Забагато невдалих спроб входу. Спробуйте знову через {whole_minutes(wait)} хв."
        return render(request, "login.html", status_code=429, error=error, **page)

    authenticated = await service.login(LoginRequest(username=username, password=password))
    if authenticated is None:
        throttle.failed(address, username)
        return render(request, "login.html", status_code=401, error="Невірний логін або пароль", **page)

    throttle.succeeded(username)
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
