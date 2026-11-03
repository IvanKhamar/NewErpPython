"""Reports for managers and the revenue feed for an outside dashboard."""

import hmac
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response

from newerp.api.deps import ClockDep, Session, enum_from_query
from newerp.api.security import Policies, require_roles
from newerp.application.reports import (
    DashboardDto,
    ProductSalesDto,
    ReportFilter,
    ReportService,
    RevenueFeedDto,
    SalesReportDto,
    StatusReportDto,
)
from newerp.application.standard_reports import Grouping, ReportDto, ReportKind, StandardReportService
from newerp.config import RevenueFeedSettings, get_settings
from newerp.domain.reports import ReportGrouping
from newerp.infrastructure.reports import SqlReportStore
from newerp.infrastructure.standard_reports import SqlStandardReportStore

router = APIRouter(prefix="/reports", tags=["Звіти"], dependencies=[Depends(require_roles(*Policies.ORDERS))])
# An outside dashboard downloads the feed, so it needs no login, only the token.
feed_router = APIRouter(tags=["Звіти"])


def get_service(session: Session, clock: ClockDep) -> ReportService:
    return ReportService(SqlReportStore(session, get_settings().language_id), clock)


Service = Annotated[ReportService, Depends(get_service)]


def get_report_filter(
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: date | None = None,
    status_id: Annotated[int | None, Query(alias="statusId")] = None,
    group: str = "Week",
) -> ReportFilter:
    return ReportFilter(from_, to, status_id, enum_from_query(ReportGrouping, group))


Filter = Annotated[ReportFilter, Depends(get_report_filter)]


def get_feed_settings() -> RevenueFeedSettings:
    return get_settings().revenue_feed


@router.get(
    "/dashboard",
    summary="Зведення для головної: замовлення й продажі сьогодні та за місяць проти попередніх, середній чек, "
    "нові покупці, графік за 30 днів, топ-5 товарів",
)
async def dashboard(service: Service) -> DashboardDto:
    return await service.dashboard()


@router.get(
    "/sales",
    summary="Продажі за періодами (group: Day, Week, Month, Year); типово — поточний місяць без скасованих замовлень",
)
async def sales(report_filter: Filter, service: Service) -> SalesReportDto:
    return await service.sales(report_filter)


@router.get(
    "/products", summary="Продані товари за кількістю (до 200); типово — поточний місяць без скасованих замовлень"
)
async def products(report_filter: Filter, service: Service) -> ProductSalesDto:
    return await service.products(report_filter)


@router.get(
    "/statuses",
    summary="Замовлення (кількість і сума) та повернення за статусами, скасовані теж; типово — поточний місяць",
)
async def statuses(report_filter: Filter, service: Service) -> StatusReportDto:
    return await service.statuses(report_filter)


@feed_router.get(
    "/feeds/revenue.json",
    summary="Виручка для зовнішнього дашборда: сьогодні, вчора, місяць проти тих самих днів минулого, рік і ряд за "
    "days днів. Токен — у заголовку X-Feed-Token або ?token=",
)
async def revenue_feed(
    service: Service,
    feed: Annotated[RevenueFeedSettings, Depends(get_feed_settings)],
    header: Annotated[str | None, Header(alias="X-Feed-Token")] = None,
    token: str | None = None,
    days: int | None = None,
) -> RevenueFeedDto:
    # Without a token in the settings there is no feed (404); a wrong token gets 401.
    if not feed.enabled:
        return Response(status_code=404)
    if not _matches(header if header is not None else token, feed.token.get_secret_value()):
        return Response(status_code=401)
    return await service.revenue_feed(30 if days is None else days, feed.max_days)


def _matches(given: str | None, expected: str) -> bool:
    """Compares in constant time, so the response time does not tell which part of the token is right."""
    return given is not None and hmac.compare_digest(given.strip().encode(), expected.strip().encode())


# ═══ OpenCart's standard reports (Python first, item 71) ═══


def get_standard_reports(session: Session, clock: ClockDep) -> StandardReportService:
    return StandardReportService(SqlStandardReportStore(session), clock)


@router.get(
    "/standard/{kind}",
    summary="Стандартні звіти OpenCart за період (from–to, типово 30 днів; group: day, week, month)",
)
async def standard_report(
    kind: ReportKind,
    service: Annotated[StandardReportService, Depends(get_standard_reports)],
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: date | None = None,
    group: Grouping = "day",
) -> ReportDto:
    return await service.report(kind, from_, to, group)
