"""Customer administration pages (Python first): the card's edit form and account section, logging in as the
customer, and the settings page with groups, approvals and custom fields. Registered before the customers module,
so /app/customers/settings is not read as a customer number."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError
from starlette.datastructures import FormData

from newerp.api.customer_admin import Admin
from newerp.api.deps import Session
from newerp.api.security import Policies
from newerp.application.customer_admin import (
    AddressEditDto,
    AddRewardRequest,
    AddTransactionRequest,
    CustomerEditDto,
    CustomerGroupEditDto,
    CustomFieldDto,
)
from newerp.web.common import BUSINESS_ERRORS, WebUser, decimal_field, is_fragment, number, page_roles, render, undo

router = APIRouter(
    prefix="/app/customers", include_in_schema=False, dependencies=[Depends(page_roles(*Policies.ORDERS))]
)
Field = Annotated[str, Form()]
setup = [Depends(page_roles(*Policies.CUSTOMER_SETUP))]
ADDRESS_FIELDS = ("firstname", "lastname", "company", "address_1", "address_2", "city", "postcode")


def _text(form: FormData, name: str) -> str:
    value = form.get(name, "")
    return value.strip() if isinstance(value, str) else ""


def _invalid(error: ValidationError) -> str:
    return "Перевірте поля: " + ", ".join(str(item["loc"][0]) for item in error.errors()) + "."


# ── Settings: groups, approvals, custom fields ──

TABS = (("groups", "Групи"), ("approvals", "Схвалення"), ("fields", "Власні поля"))


async def _settings(request: Request, admin: Admin, tab: str, **messages: Any) -> HTMLResponse:
    context: dict[str, Any] = {"tab": tab, "tabs": TABS, "groups": await admin.groups(), **messages}
    if tab == "approvals":
        context["approvals"] = await admin.approvals()
    elif tab == "fields":
        context["fields"] = await admin.custom_fields()
    template = "customers/_settings.html" if is_fragment(request) else "customers/settings.html"
    return render(request, template, setup_roles=Policies.CUSTOMER_SETUP, **context)


@router.get("/settings")
async def settings_page(request: Request, admin: Admin, tab: Literal["groups", "approvals", "fields"] = "groups"):
    return await _settings(request, admin, tab)


@router.post("/settings/approvals/{customer_id}/{action}")
async def decide(
    request: Request,
    customer_id: int,
    action: Literal["approve", "deny"],
    user: WebUser,
    admin: Admin,
    session: Session,
) -> HTMLResponse:
    try:
        await admin.decide(customer_id, action == "approve", user.user_id)
    except BUSINESS_ERRORS as error:
        return await _settings(request, admin, "approvals", error=await undo(session, error))
    return await _settings(request, admin, "approvals", saved="Збережено.")


@router.post("/settings/{kind}/{item_id}/{action}", dependencies=setup)
async def change_setting(
    request: Request,
    kind: Literal["groups", "fields"],
    item_id: int,
    action: Literal["save", "delete"],
    user: WebUser,
    admin: Admin,
    session: Session,
) -> HTMLResponse:
    form = await request.form()
    try:
        if kind == "groups" and action == "save":
            group = CustomerGroupEditDto(
                name=_text(form, "name"),
                description=_text(form, "description"),
                approval=_text(form, "approval") == "1",
                sort_order=number(_text(form, "sort_order")) or 0,
            )
            await admin.save_group(item_id or None, group)
        elif kind == "groups":
            await admin.delete_group(item_id, user.user_id)
        elif action == "save":
            field = CustomFieldDto(
                name=_text(form, "name"),
                type=_text(form, "type") or "text",
                location=_text(form, "location") or "account",
                validation=_text(form, "validation"),
                required=_text(form, "required") == "1",
                enabled=_text(form, "enabled") == "1",
                sort_order=number(_text(form, "sort_order")) or 0,
                customer_group_ids=[number(str(v)) or 0 for v in form.getlist("customer_group_ids")],
                values=_text(form, "values").splitlines(),
            )
            await admin.save_custom_field(item_id or None, field)
        else:
            await admin.delete_custom_field(item_id, user.user_id)
    except ValidationError as error:
        return await _settings(request, admin, kind, error=_invalid(error))
    except BUSINESS_ERRORS as error:
        return await _settings(request, admin, kind, error=await undo(session, error))
    return await _settings(request, admin, kind, saved="Збережено.")


# ── The card: edit, account, log in as the customer ──


async def _edit(request: Request, customer_id: int, admin: Admin, form: CustomerEditDto | None = None, **messages: Any):
    return render(
        request,
        "customers/_edit.html",
        customer_id=customer_id,
        form=form or await admin.edit_form(customer_id),
        groups=await admin.groups(),
        **messages,
    )


@router.get("/{customer_id}/edit")
async def edit_form(request: Request, customer_id: int, admin: Admin) -> HTMLResponse:
    return await _edit(request, customer_id, admin)


@router.post("/{customer_id}/edit")
async def save_customer(request: Request, customer_id: int, user: WebUser, admin: Admin, session: Session):
    form = await request.form()
    columns = [[str(v) for v in form.getlist(f"address_{name}")] for name in ("id", *ADDRESS_FIELDS)]
    default = _text(form, "default_address")
    try:
        addresses = [
            AddressEditDto(
                id=number(row[0]) or 0,
                default=row[0] == default and bool(default),
                **dict(zip(ADDRESS_FIELDS, (cell.strip() for cell in row[1:]), strict=True)),
            )
            for row in zip(*columns, strict=True)
            if any(cell.strip() for cell in row[1:]) and _text(form, f"remove_{row[0]}") != "1"
        ]
        typed = CustomerEditDto(
            firstname=_text(form, "firstname"),
            lastname=_text(form, "lastname"),
            email=_text(form, "email"),
            telephone=_text(form, "telephone"),
            customer_group_id=number(_text(form, "customer_group_id")) or 0,
            newsletter=_text(form, "newsletter") == "1",
            enabled=_text(form, "enabled") == "1",
            safe=_text(form, "safe") == "1",
            addresses=addresses,
        )
    except ValidationError as error:
        return render(request, "_error.html", error=_invalid(error))
    try:
        saved = await admin.save(customer_id, typed, user.user_id)
    except BUSINESS_ERRORS as error:
        return await _edit(request, customer_id, admin, typed, error=await undo(session, error))
    return await _edit(request, customer_id, admin, saved, saved="Збережено.")


async def _account(request: Request, customer_id: int, admin: Admin, **messages: Any) -> HTMLResponse:
    return render(
        request,
        "customers/_account.html",
        account=await admin.account(customer_id),
        login_roles=Policies.CUSTOMER_LOGIN_AS,
        **messages,
    )


@router.get("/{customer_id}/account")
async def account(request: Request, customer_id: int, admin: Admin) -> HTMLResponse:
    return await _account(request, customer_id, admin)


@router.post("/{customer_id}/account/{action}")
async def change_account(
    request: Request,
    customer_id: int,
    action: Literal["transaction", "reward", "unlock"],
    user: WebUser,
    admin: Admin,
    session: Session,
    description: Field = "",
    amount: Field = "",
) -> HTMLResponse:
    try:
        if action == "transaction":
            request_body = AddTransactionRequest(description=description, amount=decimal_field(amount, "Сума"))
            await admin.add_transaction(customer_id, request_body, user.user_id)
        elif action == "reward":
            points = int(amount) if amount.strip().lstrip("-").isdigit() else 0
            await admin.add_reward(customer_id, AddRewardRequest(description=description, points=points), user.user_id)
        else:
            await admin.unlock(customer_id, user.user_id)
    except ValidationError as error:
        return await _account(request, customer_id, admin, error=_invalid(error))
    except BUSINESS_ERRORS as error:
        return await _account(request, customer_id, admin, error=await undo(session, error))
    return await _account(request, customer_id, admin, saved="Збережено.")


@router.post("/{customer_id}/login-link", dependencies=[Depends(page_roles(*Policies.CUSTOMER_LOGIN_AS))])
async def login_link(request: Request, customer_id: int, user: WebUser, admin: Admin, session: Session):
    try:
        link = await admin.login_link(customer_id, user.user_id)
    except BUSINESS_ERRORS as error:
        return render(request, "_error.html", error=await undo(session, error))
    return render(request, "customers/_login_link.html", link=link)
