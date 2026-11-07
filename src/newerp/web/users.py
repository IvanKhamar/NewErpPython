"""Admin pages: who has access to NewErp and with which role, and the managers' bonuses (Users.razor, Bonuses.razor).

A temporary password is made on the server and shown to the admin once, on the answer of that one action."""

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from newerp.api.deps import Session
from newerp.api.security import Policies
from newerp.api.staff import get_bonus_service
from newerp.api.users import Service
from newerp.application.auth.models import CreateUserRequest, ResetPasswordRequest, SetAccessRequest
from newerp.application.auth.policy import Roles
from newerp.application.common import SystemClock
from newerp.application.staff import BonusService
from newerp.web.common import BUSINESS_ERRORS, WebUser, page_roles, render, undo

router = APIRouter(prefix="/app", include_in_schema=False)
users_only = [Depends(page_roles(*Policies.USERS))]
Bonuses = Annotated[BonusService, Depends(get_bonus_service)]
Field = Annotated[str, Form()]

ROLE_NAMES = {Roles.ADMIN: "Адміністратор", Roles.MANAGER: "Менеджер", Roles.STOREKEEPER: "Комірник"}


async def _users(request: Request, service: Service, template: str, **context: Any) -> HTMLResponse:
    response = render(request, template, users=await service.list_all(), roles=ROLE_NAMES, **context)
    if context.get("password") is not None:
        response.headers["Cache-Control"] = "no-store"  # a page with a password is not kept by the browser
    return response


@router.get("/users", dependencies=users_only)
async def users_page(request: Request, service: Service) -> HTMLResponse:
    return await _users(request, service, "users/page.html")


@router.post("/users/new", dependencies=users_only)
async def create_user(request: Request, service: Service, session: Session) -> HTMLResponse:
    form = await request.form()
    fields = ("username", "first_name", "last_name", "email", "role")
    try:
        result = await service.create(CreateUserRequest(**{name: str(form.get(name, "")).strip() for name in fields}))
    except BUSINESS_ERRORS as error:
        return await _users(request, service, "users/_main.html", error=await undo(session, error))
    return await _users(request, service, "users/_main.html", saved="Працівника додано.", password=result)


@router.post("/users/{user_id}/{action}", dependencies=users_only)
async def user_action(
    request: Request, user_id: int, action: str, user: WebUser, service: Service, session: Session, role: Field = ""
) -> HTMLResponse:
    """Give access or change the role, take the access away, or reset the password to a temporary one."""
    password = None
    try:
        if action == "access":
            password = await service.set_access(user.user_id, user_id, SetAccessRequest(role=role))
            saved = "Доступ і роль збережено."
        elif action == "revoke":
            await service.revoke(user.user_id, user_id)
            saved = "Доступ забрано; відкриті сесії закрито."
        elif action == "reset-password":
            password = await service.reset_password(user_id, ResetPasswordRequest())
            saved = "Пароль скинуто."
        else:
            return render(request, "not_found.html", status_code=404)
    except BUSINESS_ERRORS as error:
        return await _users(request, service, "users/_main.html", error=await undo(session, error))
    shown = password if password is not None and password.temporary_password else None
    return await _users(request, service, "users/_main.html", saved=saved, password=shown)


@router.get("/bonuses", dependencies=[Depends(page_roles(*Policies.BONUSES))])
async def bonuses_page(request: Request, service: Bonuses, since: str = "", until: str = "") -> HTMLResponse:
    """Managers' bonuses for a period, the current month by default."""
    today = SystemClock().now().date()
    error, report = None, None
    try:
        start = date.fromisoformat(since) if since else today.replace(day=1)
        end = date.fromisoformat(until) if until else today
        report = await service.calculate(start, end)
    except ValueError:
        error = "Дата: потрібен формат 2026-10-31."
    except BUSINESS_ERRORS as exc:
        error = str(exc)
    return render(request, "users/bonuses.html", report=report, error=error, since=since, until=until)
