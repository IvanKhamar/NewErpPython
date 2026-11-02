"""Competitor price pages: the comparison, product pages on competitors' sites and their price checks
(Competitors.razor and its dialogs)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from newerp.api.competitors import Service
from newerp.api.deps import Session
from newerp.api.security import Policies
from newerp.application.competitors import AddCompetitorLinkRequest, CompetitorPriceRequest, SaveCompetitorRequest
from newerp.web.common import BUSINESS_ERRORS, Redirect, decimal_field, number, page_roles, render, undo

router = APIRouter(
    prefix="/app/competitors", include_in_schema=False, dependencies=[Depends(page_roles(*Policies.ORDERS))]
)
Field = Annotated[str, Form()]


async def _page(request: Request, service: Service, template: str, **context: Any) -> HTMLResponse:
    return render(
        request,
        template,
        report=await service.report(),
        links=await service.links(None),
        competitors=await service.competitors(),
        **context,
    )


@router.get("")
async def competitors_page(request: Request, service: Service) -> HTMLResponse:
    return await _page(request, service, "competitors/page.html")


@router.post("/check")
async def check_all(request: Request, service: Service) -> HTMLResponse:
    """Reads the price on every active page now; the daily Celery task does the same at 07:00."""
    result = await service.check_all()
    saved = f"Перевірено сторінок: {result.checked}, ціна змінилась: {result.changed}, з помилкою: {result.failed}."
    return await _page(request, service, "competitors/_main.html", saved=saved)


@router.post("/links")
async def add_link(
    request: Request, service: Service, session: Session, competitor_id: Field = "", product_id: Field = "",
    url: Field = "",
) -> HTMLResponse:  # fmt: skip
    try:
        link = AddCompetitorLinkRequest(
            competitor_id=number(competitor_id) or 0, product_id=number(product_id) or 0, url=url.strip()
        )
        await service.add_link(link)
    except BUSINESS_ERRORS as error:
        return await _page(request, service, "competitors/_main.html", error=await undo(session, error))
    return await _page(request, service, "competitors/_main.html", saved="Сторінку додано.")


@router.post("/links/{link_id}/{action}")
async def link_action(
    request: Request, link_id: int, action: str, service: Service, session: Session, price: Field = ""
) -> HTMLResponse:
    """Check one page now, type its price by hand (for a site that hides it), or delete it with its history."""
    try:
        if action == "check":
            link = await service.check(link_id)
            saved = f"Помилка: {link.last_error}" if link.last_error else "Ціну прочитано."
        elif action == "price":
            await service.set_price(link_id, CompetitorPriceRequest(price=decimal_field(price, "Ціна")))
            saved = "Ціну записано."
        elif action == "delete":
            await service.delete_link(link_id)
            saved = "Сторінку видалено."
        else:
            return render(request, "not_found.html", status_code=404)
    except BUSINESS_ERRORS as error:
        return await _page(request, service, "competitors/_main.html", error=await undo(session, error))
    return await _page(request, service, "competitors/_main.html", saved=saved)


@router.get("/links/{link_id}/history")
async def history(request: Request, link_id: int, service: Service) -> HTMLResponse:
    try:
        prices = await service.history(link_id)
    except BUSINESS_ERRORS as error:
        return render(request, "_error.html", error=str(error))
    return render(request, "competitors/_history.html", prices=prices)


# ── A competitor: name, site and an optional price pattern ──


async def _form(request: Request, values: dict[str, Any], competitor_id: int | None, error: str | None = None):
    return render(request, "competitors/form.html", status_code=200 if error is None else 400, values=values,
                  competitor_id=competitor_id, error=error)  # fmt: skip


@router.get("/new")
async def new_competitor_page(request: Request) -> HTMLResponse:
    return await _form(request, {"is_active": True}, None)


@router.get("/{competitor_id}/edit")
async def edit_competitor_page(request: Request, competitor_id: int, service: Service) -> HTMLResponse:
    competitor = next((c for c in await service.competitors() if c.id == competitor_id), None)
    if competitor is None:
        return render(request, "not_found.html", status_code=404)
    return await _form(request, competitor.model_dump(), competitor_id)


@router.post("/save")
async def save_competitor(request: Request, service: Service, session: Session) -> HTMLResponse:
    form = await request.form()
    values = {name: str(form.get(name, "")).strip() for name in ("name", "site_url", "price_regex")}
    values["is_active"] = form.get("is_active") == "1"
    competitor_id = number(str(form.get("competitor_id", "")))
    try:
        save = SaveCompetitorRequest(**values)
        if competitor_id is None:
            await service.create_competitor(save)
        else:
            await service.update_competitor(competitor_id, save)
    except BUSINESS_ERRORS as error:
        return await _form(request, values, competitor_id, await undo(session, error))
    raise Redirect("/app/competitors")
