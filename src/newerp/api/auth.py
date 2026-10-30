import math
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from newerp.api.deps import get_auth_service, get_login_throttle
from newerp.api.errors import problem
from newerp.api.security import CurrentPrincipal, current_principal, sign_in, sign_out
from newerp.application.auth.models import ChangePasswordRequest, CurrentUser, LoginRequest
from newerp.application.auth.services import AuthService
from newerp.application.auth.throttle import LoginThrottle, whole_minutes

router = APIRouter(prefix="/auth", tags=["Вхід"])
Service = Annotated[AuthService, Depends(get_auth_service)]


@router.post("/login", response_model=CurrentUser, summary="Вхід: видає cookie сесії; після невдалих спроб — 429")
async def login(
    request: LoginRequest,
    http: Request,
    service: Service,
    throttle: Annotated[LoginThrottle, Depends(get_login_throttle)],
) -> CurrentUser | Response:
    # Behind a proxy this is the proxy's address; then the proxy must pass the client's one (see README).
    address = http.client.host if http.client else None
    wait = throttle.retry_after(address, request.username)
    if wait is not None:
        minutes = whole_minutes(wait)
        return problem(
            429,
            "Забагато невдалих спроб входу",
            f"Забагато невдалих спроб входу. Спробуйте знову через {minutes} хв.",
            headers={"Retry-After": str(math.ceil(wait.total_seconds()))},
        )

    authenticated = await service.login(request)
    if authenticated is None:
        throttle.failed(address, request.username)
        return problem(401, "Невірний логін або пароль")

    throttle.succeeded(request.username)
    sign_in(http, authenticated)
    return authenticated.user


@router.post("/logout", status_code=204, dependencies=[Depends(current_principal)], summary="Вихід: видаляє cookie")
async def logout(http: Request) -> None:
    sign_out(http)


@router.get("/me", summary="Хто зараз увійшов; mustChangePassword = true — пароль тимчасовий")
async def me(principal: CurrentPrincipal) -> CurrentUser:
    return principal.to_current_user()


@router.post("/change-password", status_code=204, summary="Змінити власний пароль; інші сесії закриваються")
async def change_password(
    request: ChangePasswordRequest, principal: CurrentPrincipal, http: Request, service: Service
) -> None:
    authenticated = await service.change_password(principal.user_id, request)
    # The new account version invalidates all sessions, so the current one is issued again.
    sign_in(http, authenticated)
