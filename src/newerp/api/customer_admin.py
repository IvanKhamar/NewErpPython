"""Customer administration (Python first, items 53 and 54). Registered before the customers router, so its fixed
paths (/customers/approvals…) are matched before /customers/{customer_id}."""

from typing import Annotated

from fastapi import APIRouter, Depends

from newerp.api.deps import ClockDep, Session
from newerp.api.security import CurrentPrincipal, Policies, require_roles
from newerp.application.customer_admin import (
    AddRewardRequest,
    AddTransactionRequest,
    ApprovalDto,
    CustomerAccountDto,
    CustomerAdminService,
    CustomerEditDto,
    CustomerGroupEditDto,
    CustomFieldDto,
    LoginLinkDto,
)
from newerp.config import get_settings
from newerp.infrastructure.customer_admin import SqlCustomerAdminStore

router = APIRouter(prefix="/customers", tags=["Покупці"], dependencies=[Depends(require_roles(*Policies.ORDERS))])
setup = [Depends(require_roles(*Policies.CUSTOMER_SETUP))]


def get_customer_admin(session: Session, clock: ClockDep) -> CustomerAdminService:
    settings = get_settings()
    store = SqlCustomerAdminStore(session, settings.language_id)
    return CustomerAdminService(store, clock, settings.customers.store_url, settings.customers.login_link_minutes)


Admin = Annotated[CustomerAdminService, Depends(get_customer_admin)]


@router.get("/approvals", summary="Покупці, що чекають на схвалення")
async def list_approvals(admin: Admin) -> list[ApprovalDto]:
    return await admin.approvals()


@router.post("/approvals/{customer_id}/approve", status_code=204, summary="Схвалити: покупець вмикається")
async def approve(customer_id: int, principal: CurrentPrincipal, admin: Admin) -> None:
    await admin.decide(customer_id, True, principal.user_id)


@router.post("/approvals/{customer_id}/deny", status_code=204, summary="Відхилити запит на схвалення")
async def deny(customer_id: int, principal: CurrentPrincipal, admin: Admin) -> None:
    await admin.decide(customer_id, False, principal.user_id)


@router.get("/groups/details", summary="Групи покупців для редагування, з кількістю покупців")
async def list_groups(admin: Admin) -> list[CustomerGroupEditDto]:
    return await admin.groups()


@router.post("/groups", status_code=201, dependencies=setup, summary="Нова група покупців")
async def create_group(request: CustomerGroupEditDto, admin: Admin) -> CustomerGroupEditDto:
    return await admin.save_group(None, request)


@router.put("/groups/{group_id}", dependencies=setup, summary="Змінити групу покупців")
async def update_group(group_id: int, request: CustomerGroupEditDto, admin: Admin) -> CustomerGroupEditDto:
    return await admin.save_group(group_id, request)


@router.delete("/groups/{group_id}", status_code=204, dependencies=setup, summary="Видалити порожню групу")
async def delete_group(group_id: int, principal: CurrentPrincipal, admin: Admin) -> None:
    await admin.delete_group(group_id, principal.user_id)


@router.get("/custom-fields", summary="Власні поля реєстрації й адреси")
async def list_custom_fields(admin: Admin) -> list[CustomFieldDto]:
    return await admin.custom_fields()


@router.post("/custom-fields", status_code=201, dependencies=setup, summary="Нове власне поле")
async def create_custom_field(request: CustomFieldDto, admin: Admin) -> CustomFieldDto:
    return await admin.save_custom_field(None, request)


@router.put("/custom-fields/{field_id}", dependencies=setup, summary="Змінити власне поле")
async def update_custom_field(field_id: int, request: CustomFieldDto, admin: Admin) -> CustomFieldDto:
    return await admin.save_custom_field(field_id, request)


@router.delete("/custom-fields/{field_id}", status_code=204, dependencies=setup, summary="Видалити власне поле")
async def delete_custom_field(field_id: int, principal: CurrentPrincipal, admin: Admin) -> None:
    await admin.delete_custom_field(field_id, principal.user_id)


@router.get("/{customer_id}/edit", summary="Покупець і його адреси для редагування")
async def edit_form(customer_id: int, admin: Admin) -> CustomerEditDto:
    return await admin.edit_form(customer_id)


@router.put("/{customer_id}", summary="Змінити покупця й адреси (пароль тут не змінюється)")
async def save_customer(
    customer_id: int, request: CustomerEditDto, principal: CurrentPrincipal, admin: Admin
) -> CustomerEditDto:
    return await admin.save(customer_id, request, principal.user_id)


@router.get("/{customer_id}/account", summary="Баланс, бонусні бали, IP і невдалі входи покупця")
async def account(customer_id: int, admin: Admin) -> CustomerAccountDto:
    return await admin.account(customer_id)


@router.post("/{customer_id}/transactions", summary="Поповнити (+) чи списати (−) баланс; в журнал аудиту")
async def add_transaction(
    customer_id: int, request: AddTransactionRequest, principal: CurrentPrincipal, admin: Admin
) -> CustomerAccountDto:
    return await admin.add_transaction(customer_id, request, principal.user_id)


@router.post("/{customer_id}/rewards", summary="Нарахувати (+) чи зняти (−) бонусні бали")
async def add_reward(
    customer_id: int, request: AddRewardRequest, principal: CurrentPrincipal, admin: Admin
) -> CustomerAccountDto:
    return await admin.add_reward(customer_id, request, principal.user_id)


@router.post("/{customer_id}/unlock", summary="Зняти блокування після невдалих входів")
async def unlock(customer_id: int, principal: CurrentPrincipal, admin: Admin) -> dict[str, int]:
    return {"removedAttempts": await admin.unlock(customer_id, principal.user_id)}


@router.post(
    "/{customer_id}/login-link",
    dependencies=[Depends(require_roles(*Policies.CUSTOMER_LOGIN_AS))],
    summary="Одноразове посилання, щоб увійти на вітрину як покупець (лише адміністратор, кілька хвилин)",
)
async def login_link(customer_id: int, principal: CurrentPrincipal, admin: Admin) -> LoginLinkDto:
    return await admin.login_link(customer_id, principal.user_id)
