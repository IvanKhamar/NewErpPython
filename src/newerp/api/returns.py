"""Product returns."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from newerp.api.deps import ClockDep, Session
from newerp.api.security import Policies, require_roles
from newerp.application.common import CreatedId, PagedResult
from newerp.application.returns import (
    AddReturnHistoryRequest,
    CreateReturnRequest,
    ReturnDetails,
    ReturnFilter,
    ReturnListItem,
    ReturnLookupsDto,
    ReturnService,
)
from newerp.config import get_settings
from newerp.infrastructure.returns import SqlReturnStore

router = APIRouter(prefix="/returns", tags=["Повернення"], dependencies=[Depends(require_roles(*Policies.ORDERS))])


def get_service(session: Session, clock: ClockDep) -> ReturnService:
    return ReturnService(SqlReturnStore(session, get_settings().language_id), clock)


Service = Annotated[ReturnService, Depends(get_service)]


@router.get(
    "",
    summary="Повернення посторінково, нові зверху; фільтри: returnId, orderId, statusId, search (клієнт, телефон, "
    "товар, модель)",
)
async def list_returns(
    service: Service,
    return_id: Annotated[int | None, Query(alias="returnId")] = None,
    order_id: Annotated[int | None, Query(alias="orderId")] = None,
    status_id: Annotated[int | None, Query(alias="statusId")] = None,
    search: str | None = None,
    page: int = 1,
    page_size: Annotated[int, Query(alias="pageSize")] = 20,
) -> PagedResult[ReturnListItem]:
    return await service.list_page(
        ReturnFilter(
            return_id=return_id, order_id=order_id, status_id=status_id, search=search, page=page, page_size=page_size
        )
    )


@router.get("/lookups", summary="Статуси, причини й дії повернень")
async def lookups(service: Service) -> ReturnLookupsDto:
    return await service.lookups()


@router.get("/{return_id}", summary="Повернення з журналом статусів")
async def get_return(return_id: int, service: Service) -> ReturnDetails:
    return await service.get(return_id)


@router.post(
    "",
    status_code=201,
    summary="Оформити повернення товару із замовлення: клієнт і товар беруться із замовлення, кількість — не більше, "
    "ніж ще можна повернути",
)
async def create_return(request: CreateReturnRequest, service: Service, response: Response) -> CreatedId:
    return_id = await service.create(request)
    response.headers["Location"] = f"/returns/{return_id}"
    return CreatedId(id=return_id)


@router.post(
    "/{return_id}/history",
    status_code=204,
    summary="Новий статус повернення з коментарем у журнал; actionId — змінити дію (повернено кошти, обмін…)",
)
async def add_history(return_id: int, request: AddReturnHistoryRequest, service: Service) -> None:
    await service.add_history(return_id, request)
