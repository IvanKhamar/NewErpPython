"""Partner pages: resellers, their XML feed and what goes into it (Partners.razor, PartnerCard.razor)."""

from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from starlette.datastructures import FormData

from newerp.api.deps import Session
from newerp.api.partners import Repository, Service
from newerp.api.security import Policies
from newerp.application.partners import CategoryMapRow, PartnerDto, ProductMapRow, SavePartnerRequest
from newerp.domain.common import DomainError
from newerp.web.common import BUSINESS_ERRORS, Redirect, decimal_field, number, page_roles, render, undo

router = APIRouter(
    prefix="/app/partners", include_in_schema=False, dependencies=[Depends(page_roles(*Policies.ORDERS))]
)


@router.get("")
async def partner_list(request: Request, repository: Repository) -> HTMLResponse:
    return render(request, "partners/list.html", partners=await repository.list_all())


async def _card(request: Request, repository: Repository, partner: PartnerDto | None) -> HTMLResponse:
    return render(request, "partners/card.html", partner=partner, categories=await repository.list_categories())


@router.get("/new")
async def new_partner_page(request: Request, repository: Repository) -> HTMLResponse:
    return await _card(request, repository, None)


# ── Rows added to the form; the server checks them only when the partner is saved ──


@router.get("/category-row")
async def category_row(request: Request, repository: Repository, add: str = "") -> HTMLResponse:
    category = next((c for c in await repository.list_categories() if c.id == number(add)), None)
    if category is None:
        return HTMLResponse("")
    return render(request, "partners/_category_row.html", row={"category_id": category.id}, category=category)


@router.get("/products")
async def search_products(request: Request, service: Service, product_search: str = "") -> HTMLResponse:
    found = await service.search_products(product_search) if product_search.strip() else []
    return render(request, "partners/_products.html", products=found)


@router.get("/product-row")
async def product_row(request: Request, product_id: int, name: str = "", size: str = "") -> HTMLResponse:
    row = {"product_id": product_id, "product_name": name, "size_name": size or None}
    return render(request, "partners/_product_row.html", row=row)


@router.get("/{partner_id}")
async def partner_card(request: Request, partner_id: int, repository: Repository) -> HTMLResponse:
    partner = await repository.get_dto(partner_id)
    if partner is None:
        return render(request, "not_found.html", status_code=404)
    return await _card(request, repository, partner)


def _text(form: FormData, name: str) -> str:
    value = form.get(name, "")
    return value.strip() if isinstance(value, str) else ""


def _column(form: FormData, name: str) -> list[str]:
    return [str(value).strip() for value in form.getlist(name)]


def _optional(value: str, field: str) -> Decimal | None:
    return decimal_field(value, field) if value else None


def _partner_request(form: FormData) -> SavePartnerRequest:
    """The partner with both maps; each map comes as columns of repeated fields."""
    categories = zip(*(_column(form, f"category_{name}") for name in
                       ("id", "size", "external_id", "external_name", "supply", "sale")), strict=True)  # fmt: skip
    products = zip(*(_column(form, f"product_{name}") for name in
                     ("id", "size", "external_id", "external_name", "supply", "sale")), strict=True)  # fmt: skip
    quantity = decimal_field(_text(form, "default_quantity"), "Кількість у фіді")
    if quantity != quantity.to_integral_value():
        raise DomainError("Кількість у фіді: потрібне ціле число.")
    return SavePartnerRequest(
        code=_text(form, "code"),
        name=_text(form, "name"),
        is_active=form.get("is_active") == "1",
        feed_url=_text(form, "feed_url"),
        feed_enabled=form.get("feed_enabled") == "1",
        vendor_name=_text(form, "vendor_name"),
        notes=_text(form, "notes"),
        default_quantity=int(quantity),
        category_map=[
            CategoryMapRow(
                category_id=number(category) or 0,
                size_name=size or None,
                external_id=external_id,
                external_name=external_name,
                percent_supply=_optional(supply, "Відсоток постачання"),
                percent_sale=_optional(sale, "Відсоток продажу"),
            )  # fmt: skip
            for category, size, external_id, external_name, supply, sale in categories
        ],
        product_map=[
            ProductMapRow(
                product_id=number(product) or 0,
                size_name=size or None,
                external_id=external_id,
                external_name=external_name,
                price_supply=_optional(supply, "Ціна постачання"),
                price_sale=_optional(sale, "Ціна продажу"),
            )  # fmt: skip
            for product, size, external_id, external_name, supply, sale in products
        ],
    )


@router.post("/save")
async def save_partner(request: Request, service: Service, session: Session) -> HTMLResponse:
    form = await request.form()
    partner_id = number(_text(form, "partner_id"))
    try:
        if partner_id is None:
            partner_id = await service.create(_partner_request(form))
        else:
            await service.update(partner_id, _partner_request(form))
    except BUSINESS_ERRORS as error:
        return render(request, "_error.html", error=await undo(session, error))  # the form keeps its rows
    raise Redirect(f"/app/partners/{partner_id}")


@router.post("/{partner_id}/delete")
async def delete_partner(request: Request, partner_id: int, service: Service, session: Session) -> HTMLResponse:
    try:
        await service.delete(partner_id)
    except BUSINESS_ERRORS as error:
        return render(request, "_error.html", error=await undo(session, error))
    raise Redirect("/app/partners")
