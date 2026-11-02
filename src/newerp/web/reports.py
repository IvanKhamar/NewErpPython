"""Report pages: sales by period, products sold, orders and returns by status (Reports.razor)."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from newerp.api.deps import get_order_queries
from newerp.api.reports import Service
from newerp.api.security import Policies
from newerp.application.reports import ReportFilter
from newerp.domain.reports import ReportGrouping
from newerp.infrastructure.orders.queries import SqlOrderQueries
from newerp.web.common import BUSINESS_ERRORS, number, page_roles, render

router = APIRouter(prefix="/app/reports", include_in_schema=False, dependencies=[Depends(page_roles(*Policies.ORDERS))])
Queries = Annotated[SqlOrderQueries, Depends(get_order_queries)]

VIEWS = {"sales": "Продажі", "products": "Товари", "statuses": "Статуси"}
GROUPS = {ReportGrouping.DAY: "днях", ReportGrouping.WEEK: "тижнях", ReportGrouping.MONTH: "місяцях",
          ReportGrouping.YEAR: "роках"}  # fmt: skip


def _date(value: str) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


@router.get("")
async def reports_page(
    request: Request,
    service: Service,
    queries: Queries,
    view: str = "sales",
    since: str = "",
    until: str = "",
    status: str = "",
    group: str = "1",
) -> HTMLResponse:
    """One page with three reports; the filters stay in the address, so a report can be bookmarked."""
    view = view if view in VIEWS else "sales"
    try:
        grouping = ReportGrouping(number(group) if number(group) is not None else 1)
    except ValueError:
        grouping = ReportGrouping.WEEK
    report_filter = ReportFilter(_date(since), _date(until), number(status), grouping)
    error, report = None, None
    try:
        if view == "sales":
            report = await service.sales(report_filter)
        elif view == "products":
            report = await service.products(report_filter)
        else:
            report = await service.statuses(report_filter)
    except BUSINESS_ERRORS as exc:
        error = str(exc)
    values = {"view": view, "since": since, "until": until, "status": number(status), "group": grouping}
    return render(request, "reports/page.html", report=report, error=error, values=values, views=VIEWS,
                  groups=GROUPS, statuses=await queries.list_statuses())  # fmt: skip
