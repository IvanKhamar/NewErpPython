"""UTM report, the remarketing data of an order and reports on what the storefront records (Python first). Access as
to orders."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from newerp.api.deps import ClockDep, Session
from newerp.api.reports import Filter
from newerp.api.security import Policies, require_roles
from newerp.application.remarketing import OrderRemarketingDto, RemarketingService, UtmFilter, UtmReportDto
from newerp.application.store_reports import (
    CustomerRewardDto,
    OnlineVisitorDto,
    ProductViewsDto,
    StoreReportService,
    TaxReportDto,
)
from newerp.config import get_settings
from newerp.infrastructure.remarketing import SqlRemarketingStore
from newerp.infrastructure.store_reports import SqlStoreReportStore

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


# ═══ What the storefront records (Python first) ═══


def get_store_reports(session: Session, clock: ClockDep) -> StoreReportService:
    return StoreReportService(SqlStoreReportStore(session, get_settings().language_id), clock)


StoreReports = Annotated[StoreReportService, Depends(get_store_reports)]


@router.get("/online", summary="Хто зараз на сайті (вітрина пише, якщо увімкнено Customers Online; година)")
async def online(service: StoreReports) -> list[OnlineVisitorDto]:
    return await service.online()


@router.get("/product-views", summary="Найпереглядуваніші товари: перегляди, продано штук і продажі на 100 переглядів")
async def product_views(service: StoreReports) -> list[ProductViewsDto]:
    return await service.product_views()


@router.get("/rewards", summary="Бонусні бали покупців: залишок (нараховано мінус витрачено), найбільші зверху")
async def rewards(service: StoreReports) -> list[CustomerRewardDto]:
    return await service.rewards()


@router.get("/taxes", summary="Податки з підсумків замовлень (рядки tax) за період і назвою податку")
async def taxes(report_filter: Filter, service: StoreReports) -> TaxReportDto:
    return await service.taxes(report_filter)
