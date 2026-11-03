"""Pages of the last items (Python first): status lists (69, the admin only), banners and SEO addresses (70), and
OpenCart's standard reports (71)."""

from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError
from starlette.datastructures import FormData

from newerp.api.administration import StatusLists
from newerp.api.catalog import Banners, SeoUrls
from newerp.api.deps import Session
from newerp.api.reports import get_standard_reports
from newerp.api.security import Policies
from newerp.application.banners import BannerDto, BannerImageDto, SeoUrlDto
from newerp.application.standard_reports import TITLES, StandardReportService
from newerp.application.status_lists import StatusItemDto, StatusList
from newerp.web.common import BUSINESS_ERRORS, WebUser, is_fragment, number, page_roles, render, undo

router = APIRouter(prefix="/app", include_in_schema=False, dependencies=[Depends(page_roles(*Policies.ORDERS))])
admin_only = [Depends(page_roles(*Policies.ADMIN_TOOLS))]
STATUS_LISTS = (
    ("order-statuses", "Статуси замовлень"),
    ("return-statuses", "Статуси повернень"),
    ("return-reasons", "Причини повернень"),
    ("return-actions", "Дії з поверненнями"),
    ("stock-statuses", "Статуси наявності"),
)


def _text(form: FormData, name: str) -> str:
    value = form.get(name, "")
    return value.strip() if isinstance(value, str) else ""


# ── 69: status lists ──


async def _statuses(request: Request, service: StatusLists, kind: StatusList, **messages: Any) -> HTMLResponse:
    template = "lists/_statuses.html" if is_fragment(request) else "lists/statuses.html"
    return render(request, template, kind=kind, kinds=STATUS_LISTS, items=await service.items(kind), **messages)


@router.get("/admin/statuses", dependencies=admin_only)
async def statuses_page(request: Request, service: StatusLists, kind: StatusList = "order-statuses") -> HTMLResponse:
    return await _statuses(request, service, kind)


@router.post("/admin/statuses/{kind}/{item_id}/{action}", dependencies=admin_only)
async def change_status(
    request: Request,
    kind: StatusList,
    item_id: int,
    action: Literal["save", "delete"],
    user: WebUser,
    service: StatusLists,
    session: Session,
) -> HTMLResponse:
    form = await request.form()
    try:
        if action == "delete":
            await service.delete(kind, item_id, user.user_id)
        else:
            item = StatusItemDto(name=_text(form, "name"), color=_text(form, "color"))
            await service.save(kind, item_id or None, item, user.user_id)
    except ValidationError:
        return await _statuses(request, service, kind, error="Назва — до 32 знаків.")
    except BUSINESS_ERRORS as error:
        return await _statuses(request, service, kind, error=await undo(session, error))
    return await _statuses(request, service, kind, saved="Збережено.")


# ── 70: banners and SEO addresses ──


async def _banners(request: Request, banners: Banners, seo: SeoUrls, search: str = "", **messages: Any) -> HTMLResponse:
    template = "lists/_banners.html" if is_fragment(request) else "lists/banners.html"
    return render(
        request,
        template,
        banners=await banners.banners(),
        seo=await seo.page(search or None, 1, 100),
        search=search,
        **messages,
    )


@router.get("/catalog/banners")
async def banners_page(request: Request, banners: Banners, seo: SeoUrls, search: str = "") -> HTMLResponse:
    return await _banners(request, banners, seo, search)


@router.post("/catalog/banners/{banner_id}/{action}")
async def change_banner(
    request: Request,
    banner_id: int,
    action: Literal["save", "delete"],
    user: WebUser,
    banners: Banners,
    seo: SeoUrls,
    session: Session,
) -> HTMLResponse:
    form = await request.form()
    try:
        if action == "delete":
            await banners.delete(banner_id, user.user_id)
        else:
            columns = [[str(v) for v in form.getlist(f"image_{n}")] for n in ("title", "link", "image", "sort_order")]
            images = [
                BannerImageDto(title=title, link=link, image=image, sort_order=number(order) or 0)
                for title, link, image, order in zip(*columns, strict=True)
            ]
            banner = BannerDto(name=_text(form, "name"), enabled=_text(form, "enabled") == "1", images=images)
            await banners.save(banner_id or None, banner, user.user_id)
    except ValidationError:
        return await _banners(request, banners, seo, error="Перевірте поля банера.")
    except BUSINESS_ERRORS as error:
        return await _banners(request, banners, seo, error=await undo(session, error))
    return await _banners(request, banners, seo, saved="Збережено.")


@router.post("/catalog/seo-urls/{seo_url_id}/{action}")
async def change_seo_url(
    request: Request,
    seo_url_id: int,
    action: Literal["save", "delete"],
    user: WebUser,
    banners: Banners,
    seo: SeoUrls,
    session: Session,
) -> HTMLResponse:
    form = await request.form()
    try:
        if action == "delete":
            await seo.delete(seo_url_id, user.user_id)
        else:
            item = SeoUrlDto(query=_text(form, "query"), keyword=_text(form, "keyword"))
            await seo.save(seo_url_id or None, item, user.user_id)
    except ValidationError:
        return await _banners(request, banners, seo, error="Перевірте поля адреси.")
    except BUSINESS_ERRORS as error:
        return await _banners(request, banners, seo, error=await undo(session, error))
    return await _banners(request, banners, seo, saved="Збережено.")


# ── 71: standard reports ──

Reports = Annotated[StandardReportService, Depends(get_standard_reports)]


@router.get("/reports/standard")
async def standard_reports_page(
    request: Request,
    reports: Reports,
    kind: str = "sales-orders",
    date_from: str = "",
    date_to: str = "",
    group: str = "day",
) -> HTMLResponse:
    """The form's values come as text; a wrong one is shown as an error under it, not a 422 page."""
    error = None
    report = None
    try:
        start = date.fromisoformat(date_from) if date_from else None
        end = date.fromisoformat(date_to) if date_to else None
        if kind not in TITLES:
            raise ValueError
        report = await reports.report(kind, start, end, group if group in ("day", "week", "month") else "day")  # type: ignore[arg-type]
    except ValueError:
        error = "Перевірте звіт і дати (РРРР-ММ-ДД)."
    except BUSINESS_ERRORS as exc:
        error = str(exc)
    template = "lists/_report.html" if is_fragment(request) else "lists/report.html"
    return render(
        request,
        template,
        report=report,
        error=error,
        kinds=TITLES,
        kind=kind,
        group=group,
        date_from=date_from,
        date_to=date_to,
    )
