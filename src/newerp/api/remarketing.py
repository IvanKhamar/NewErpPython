"""UTM report and the remarketing data of an order (Python first). Access as to orders."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from newerp.api.deps import ClockDep, Session
from newerp.api.reports import Filter
from newerp.api.security import Policies, require_roles
from newerp.application.remarketing import OrderRemarketingDto, RemarketingService, UtmFilter, UtmReportDto
from newerp.infrastructure.remarketing import SqlRemarketingStore

orders_access = [Depends(require_roles(*Policies.ORDERS))]
router = APIRouter(prefix="/reports", tags=["Звіти"], dependencies=orders_access)
order_router = APIRouter(prefix="/orders/{order_id}", tags=["Замовлення"], dependencies=orders_access)


def get_service(session: Session, clock: ClockDep) -> RemarketingService:
    return RemarketingService(SqlRemarketingStore(session), clock)


Service = Annotated[RemarketingService, Depends(get_service)]


@router.get(
    "/utm",
    summary="Замовлення за мітками UTM (таблиця модуля ремаркетингу вітрини): за період і групою; bySource=true — "
    "рядок на кожне джерело",
)
async def utm_report(
    report_filter: Filter,
    service: Service,
    source: str | None = None,
    medium: str | None = None,
    campaign: str | None = None,
    by_source: Annotated[bool, Query(alias="bySource")] = False,
) -> UtmReportDto:
    return await service.utm_report(report_filter, UtmFilter(source, medium, campaign, by_source))


@order_router.get("/remarketing", summary="Мітки UTM замовлення й час, коли пікселі вітрини надіслали події")
async def order_remarketing(order_id: int, service: Service) -> OrderRemarketingDto:
    return await service.for_order(order_id)
