"""The administration page (Python first, items 63–68), the admin only: tabs for the store settings, the OpenCart
user groups and API users, the error log, the backup and the uploaded files. Downloads (backup, log, files) are
plain links to the API, which checks the same cookie and role."""

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError
from starlette.datastructures import FormData

from newerp.api.administration import ApiUsers, Backup, ErrorLog, StoreSettings, Uploads, UserGroups
from newerp.api.deps import Session
from newerp.api.security import Policies
from newerp.application.opencart_access import ApiUserDto, UserGroupDto
from newerp.web.common import BUSINESS_ERRORS, WebUser, is_fragment, page_roles, render, undo

router = APIRouter(
    prefix="/app/admin", include_in_schema=False, dependencies=[Depends(page_roles(*Policies.ADMIN_TOOLS))]
)
TABS = (("settings", "Налаштування магазину"), ("groups", "Групи OpenCart"), ("api", "API"), ("log", "Журнал помилок"),
        ("backup", "Резервна копія"), ("uploads", "Файли покупців"))  # fmt: skip
Tab = Literal["settings", "groups", "api", "log", "backup", "uploads"]


def _text(form: FormData, name: str) -> str:
    value = form.get(name, "")
    return value.strip() if isinstance(value, str) else ""


async def _tab(
    request: Request, tab: str, settings: StoreSettings, groups: UserGroups, apis: ApiUsers, log: ErrorLog,
    backup: Backup, uploads: Uploads, **context: Any,
) -> HTMLResponse:  # fmt: skip
    data: dict[str, Any] = {"tab": tab, "tabs": TABS, **context}
    if tab == "settings":
        data |= {"groups": await settings.groups(), "action": "/app/admin/settings", "target": "admin-tab"}
    elif tab == "groups":
        data |= {"user_groups": await groups.groups(), "routes": await groups.known_routes()}
    elif tab == "api":
        data["api_users"] = await apis.users()
    elif tab == "log":
        data["log"] = log.view()
    elif tab == "backup":
        data["tables"] = await backup.tables()
    else:
        data["uploads"] = await uploads.uploads()
    template = "admin/_tab.html" if is_fragment(request) else "admin/page.html"
    return render(request, template, **data)


@router.get("")
async def admin_page(
    request: Request, settings: StoreSettings, groups: UserGroups, apis: ApiUsers, log: ErrorLog, backup: Backup,
    uploads: Uploads, tab: Tab = "settings",
) -> HTMLResponse:  # fmt: skip
    return await _tab(request, tab, settings, groups, apis, log, backup, uploads)


@router.post("/settings")
async def save_settings(
    request: Request, user: WebUser, session: Session, settings: StoreSettings, groups: UserGroups, apis: ApiUsers,
    log: ErrorLog, backup: Backup, uploads: Uploads,
) -> HTMLResponse:  # fmt: skip
    form = await request.form()
    current = {s.key: s.value for g in await settings.groups() for s in g.settings if s.kind == "text"}
    changes = {k: v for k, v in form.multi_items() if isinstance(v, str) and k in current and v.strip() != current[k]}
    services = (settings, groups, apis, log, backup, uploads)
    try:
        await settings.save(changes, user.user_id)
    except BUSINESS_ERRORS as error:
        return await _tab(request, "settings", *services, error=await undo(session, error))
    return await _tab(request, "settings", *services, saved="Збережено.")


@router.post("/{kind}/{item_id}/{action}")
async def change(
    request: Request, kind: Literal["groups", "api", "log", "uploads"], item_id: int,
    action: Literal["save", "delete", "key", "clear"], user: WebUser, session: Session, settings: StoreSettings,
    groups: UserGroups, apis: ApiUsers, log: ErrorLog, backup: Backup, uploads: Uploads,
) -> HTMLResponse:  # fmt: skip
    """item_id 0 with save adds a new group or API user."""
    form = await request.form()
    services = (settings, groups, apis, log, backup, uploads)
    extra: dict[str, Any] = {}
    try:
        if kind == "groups" and action == "save":
            group = UserGroupDto(
                name=_text(form, "name"),
                access=[str(v) for v in form.getlist("access")] + _text(form, "access_more").split(),
                modify=[str(v) for v in form.getlist("modify")] + _text(form, "modify_more").split(),
            )
            await groups.save(item_id or None, group, user.user_id)
        elif kind == "groups" and action == "delete":
            await groups.delete(item_id, user.user_id)
        elif kind == "api" and action == "save":
            api_user = ApiUserDto(
                username=_text(form, "username"), enabled=_text(form, "enabled") == "1",
                ips=_text(form, "ips").replace(",", " ").split(),
            )  # fmt: skip
            await apis.save(item_id or None, api_user, user.user_id)
        elif kind == "api" and action == "key":
            extra["new_key"] = await apis.new_key(item_id, user.user_id)
        elif kind == "api" and action == "delete":
            await apis.delete(item_id, user.user_id)
        elif kind == "log" and action == "clear":
            log.clear(user.user_id)
        elif kind == "uploads" and action == "delete":
            await uploads.delete(item_id, user.user_id)
    except ValidationError:
        return await _tab(request, kind, *services, error="Перевірте поля.")
    except BUSINESS_ERRORS as error:
        return await _tab(request, kind, *services, error=await undo(session, error))
    return await _tab(request, kind, *services, saved="Збережено.", **extra)
