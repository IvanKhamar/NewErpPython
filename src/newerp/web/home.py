"""Home page: the dashboard for those who work with orders (Home.razor + DashboardPanel.razor)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from newerp.api.reports import get_service
from newerp.api.security import Policies
from newerp.api.stock import Repository as StockRepository
from newerp.application.reports import ReportService
from newerp.application.stock.ports import StockItemFilter
from newerp.web.common import WebUser, render

router = APIRouter(prefix="/app", include_in_schema=False)


@router.get("/")
async def home(
    request: Request,
    user: WebUser,
    reports: Annotated[ReportService, Depends(get_service)],
    stock: StockRepository,
) -> HTMLResponse:
    # The storekeeper sees at once what is below its minimum (the low-stock alert in the app).
    low = await stock.list_items(StockItemFilter(below_minimum_only=True)) if user.role in Policies.STOCK else []
    if user.role not in Policies.ORDERS:
        return render(request, "home.html", low=low)

    dashboard = await reports.dashboard()
    tiles = [
        # (title, tile, is money)
        ("Замовлення сьогодні", dashboard.orders_today, False),
        ("Продажі сьогодні", dashboard.sales_today, True),
        ("Замовлення за місяць", dashboard.orders_month, False),
        ("Продажі за місяць", dashboard.sales_month, True),
        ("Середній чек за місяць", dashboard.average_check_month, True),
        ("Нові покупці за місяць", dashboard.new_customers_month, False),
    ]
    # The 30-day chart: each bar's height in percent of the best day.
    best = max((day.total for day in dashboard.last30_days), default=0)
    bars = [(day, int(day.total * 100 / best) if best else 0) for day in dashboard.last30_days]
    return render(request, "home.html", dashboard=dashboard, tiles=tiles, bars=bars, low=low)
