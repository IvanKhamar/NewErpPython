"""Registered customers with their orders, totals, risk and managers' notes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from newerp.api.deps import ClockDep, Session
from newerp.api.security import Policies, require_roles
from newerp.application.common import PagedResult
from newerp.application.customers import (
    AddCustomerNoteRequest,
    CustomerDetails,
    CustomerFilter,
    CustomerGroupDto,
    CustomerListItem,
    CustomerService,
)
from newerp.config import get_settings
from newerp.infrastructure.customers import SqlCustomerStore

router = APIRouter(prefix="/customers", tags=["Покупці"], dependencies=[Depends(require_roles(*Policies.ORDERS))])


def get_service(session: Session, clock: ClockDep) -> CustomerService:
    return CustomerService(SqlCustomerStore(session, get_settings().language_id), clock)


Service = Annotated[CustomerService, Depends(get_service)]


@router.get(
    "",
    summary="Зареєстровані покупці посторінково, нові зверху; фільтри: search (ім'я, e-mail, телефон, id), groupId, "
    "status",
)
async def list_customers(
    service: Service,
    search: str | None = None,
    group_id: Annotated[int | None, Query(alias="groupId")] = None,
    status: bool | None = None,
    page: int = 1,
    page_size: Annotated[int, Query(alias="pageSize")] = 20,
) -> PagedResult[CustomerListItem]:
    return await service.list_page(
        CustomerFilter(search=search, group_id=group_id, status=status, page=page, page_size=page_size)
    )


@router.get("/groups", summary="Групи покупців")
async def groups(service: Service) -> list[CustomerGroupDto]:
    return await service.groups()


@router.get(
    "/{customer_id}",
    summary="Картка покупця: адреси, замовлення (за покупцем і тим самим телефоном), підсумки, ризик і нотатки",
)
async def get_customer(customer_id: int, service: Service) -> CustomerDetails:
    return await service.get(customer_id)


@router.post("/{customer_id}/notes", status_code=204, summary="Нотатка менеджера про покупця")
async def add_note(customer_id: int, request: AddCustomerNoteRequest, service: Service) -> None:
    await service.add_note(customer_id, request)
