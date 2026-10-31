"""Size pre-orders: limits, expected dates, and taking what was sold ahead off the limits."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from newerp.api.deps import Session
from newerp.api.security import Policies, require_roles
from newerp.application.catalog.pre_orders import (
    PreOrderFilter,
    PreOrderRow,
    PreOrderService,
    PreOrderSettleResult,
    UpdatePreOrderRequest,
)
from newerp.config import get_settings
from newerp.infrastructure.catalog.pre_orders import SqlPreOrderStore

router = APIRouter(
    prefix="/preorders", tags=["Передзамовлення"], dependencies=[Depends(require_roles(*Policies.ORDERS))]
)


def get_service(session: Session) -> PreOrderService:
    return PreOrderService(SqlPreOrderStore(session, get_settings().language_id))


Service = Annotated[PreOrderService, Depends(get_service)]


@router.get(
    "",
    summary="Розміри товарів (до 200) з залишком, лімітом передзамовлення, очікуваною датою й тим, як їх продає "
    "вітрина",
)
async def list_pre_orders(
    service: Service,
    search: str | None = None,
    only_with_quota: Annotated[bool, Query(alias="onlyWithQuota")] = False,
) -> list[PreOrderRow]:
    return await service.list_rows(PreOrderFilter(search=search, only_with_quota=only_with_quota))


@router.put(
    "/{product_option_value_id}",
    status_code=204,
    summary="Ліміт передзамовлення розміру (0 — не продавати наперед) і дата, коли розмір очікується",
)
async def update_pre_order(product_option_value_id: int, request: UpdatePreOrderRequest, service: Service) -> None:
    await service.update(product_option_value_id, request)


@router.post(
    "/settle",
    summary="Списати продане наперед (мінусовий залишок) з лімітів; фонова задача робить це сама що "
    "settle_interval_minutes",
)
async def settle(service: Service) -> PreOrderSettleResult:
    return await service.settle()
