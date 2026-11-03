"""What every page shares: the templates, the signed-in user, the menu (like MainLayout.razor + Access.cs)."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote, urlsplit

from fastapi import Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.api.deps import UserLoginStoreDep
from newerp.api.errors import NotAuthenticated
from newerp.api.security import Policies, Principal, current_principal
from newerp.application.auth.policy import Roles
from newerp.application.common import ConflictError, NotFoundError
from newerp.domain.common import DomainError

HERE = Path(__file__).resolve().parent
# Jinja2 escapes every {{ value }} in .html templates, so text from the database cannot inject HTML (XSS).
templates = Jinja2Templates(directory=HERE / "templates")

# What a page shows to the user instead of a 4xx JSON answer.
BUSINESS_ERRORS = (DomainError, NotFoundError, ConflictError)


def money(value: Decimal | float | int) -> str:
    """1234.5 → "1 234,50", as Ukrainians write amounts."""
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


def when(value: datetime | date) -> str:
    return f"{value:%d.%m.%Y %H:%M}" if isinstance(value, datetime) else f"{value:%d.%m.%Y}"


def qty(value: Decimal | int) -> str:
    """A stock quantity without trailing zeros: 12.500 → "12,5"."""
    return f"{value:.3f}".rstrip("0").rstrip(".").replace(".", ",")


templates.env.filters["money"] = money
templates.env.filters["when"] = when
templates.env.filters["qty"] = qty


@dataclass(frozen=True)
class MenuLink:
    title: str
    href: str
    roles: tuple[str, ...]


# Each link is shown only to the roles the API lets into that module.
MENU = (
    MenuLink("Головна", "/app/", Roles.ALL),
    MenuLink("Замовлення", "/app/orders", Policies.ORDERS),
    MenuLink("Нове замовлення", "/app/orders/new", Policies.ORDERS),
    MenuLink("Повернення", "/app/returns", Policies.ORDERS),
    MenuLink("Покупці", "/app/customers", Policies.ORDERS),
    MenuLink("Налаштування покупців", "/app/customers/settings", Policies.ORDERS),
    MenuLink("Доставка", "/app/delivery", Policies.ORDERS),
    MenuLink("Товари", "/app/catalog/products", Policies.ORDERS),
    MenuLink("Порядок товарів", "/app/catalog/sort", Policies.ORDERS),
    MenuLink("Категорії", "/app/catalog/categories", Policies.ORDERS),
    MenuLink("Мітки й пресети пошуку", "/app/catalog/labels", Policies.ORDERS),
    MenuLink("Фільтри, атрибути, опції", "/app/catalog/references", Policies.ORDERS),
    MenuLink("Банери й SEO-адреси", "/app/catalog/banners", Policies.ORDERS),
    MenuLink("Вміст вітрини й відгуки", "/app/catalog/content", Policies.ORDERS),
    MenuLink("Масові ціни й імпорт", "/app/catalog/prices", Policies.CATALOG_BULK),
    MenuLink("Передзамовлення", "/app/preorders", Policies.ORDERS),
    MenuLink("Дні народження", "/app/marketing/birthdays", Policies.ORDERS),
    MenuLink("Вивантаження контактів", "/app/marketing/export", Policies.ORDERS),
    MenuLink("Подарункові сертифікати", "/app/marketing/vouchers", Policies.ORDERS),
    MenuLink("Купони", "/app/marketing/coupons", Policies.ORDERS),
    MenuLink("Коди відстеження", "/app/marketing/tracking", Policies.ORDERS),
    MenuLink("Теми сертифікатів", "/app/marketing/vouchers/themes", Policies.ORDERS),
    MenuLink("Інтеграції", "/app/marketing/integrations", Policies.INTEGRATIONS),
    MenuLink("Ремаркетинг", "/app/marketing/remarketing", Policies.INTEGRATIONS),
    MenuLink("Партнери", "/app/partners", Policies.ORDERS),
    MenuLink("Ціни конкурентів", "/app/competitors", Policies.ORDERS),
    MenuLink("Звіти", "/app/reports", Policies.ORDERS),
    MenuLink("Стандартні звіти", "/app/reports/standard", Policies.ORDERS),
    MenuLink("Каса", "/app/cashbox", Policies.FINANCE),
    MenuLink("Надходження на рахунок", "/app/payments", Policies.FINANCE),
    MenuLink("Розхідники", "/app/stock", Policies.STOCK),
    MenuLink("За складами", "/app/stock/by-warehouse", Policies.STOCK),
    MenuLink("Інвентаризація", "/app/stock/stocktaking", Policies.STOCK),
    MenuLink("Що дозамовити", "/app/stock/reorder", Policies.STOCK),
    MenuLink("Замовлення постачальникам", "/app/stock/purchase-orders", Policies.STOCK),
    MenuLink("Постачальники", "/app/stock/suppliers", Policies.STOCK),
    MenuLink("Моделі", "/app/production", Policies.STOCK),
    MenuLink("Пошите за складами", "/app/production/by-warehouse", Policies.STOCK),
    MenuLink("План пошиву", "/app/production/plan", Policies.STOCK),
    MenuLink("Журнал крою й пошиву", "/app/production/log", Policies.STOCK),
    MenuLink("Бонуси", "/app/bonuses", Policies.BONUSES),
    MenuLink("Користувачі", "/app/users", Policies.USERS),
    MenuLink("Адміністрування", "/app/admin", Policies.ADMIN_TOOLS),
    MenuLink("Довідники статусів", "/app/admin/statuses", Policies.ADMIN_TOOLS),
)


def render(request: Request, template: str, status_code: int = 200, **context: Any) -> HTMLResponse:
    user: Principal | None = getattr(request.state, "web_user", None)
    menu = [link for link in MENU if user is not None and user.role in link.roles]
    return templates.TemplateResponse(
        request, template, {"user": user, "menu": menu, **context}, status_code=status_code
    )


def is_fragment(request: Request) -> bool:
    """HTMX asks for a piece of the page; after a reload the back button (history restore) needs all of it."""
    return "HX-Request" in request.headers and "HX-History-Restore-Request" not in request.headers


def number(value: str | None) -> int | None:
    """A form or query field as an id: empty or not a number → None (an empty <select> sends "")."""
    return int(value) if value and value.isascii() and value.isdigit() else None


def decimal_field(value: str, field: str) -> Decimal:
    """A number typed in a form: a comma is allowed (99,50); empty is 0."""
    try:
        amount = Decimal((value or "0").replace(",", ".").replace(" ", ""))
    except InvalidOperation:
        amount = Decimal("NaN")
    if not amount.is_finite():
        raise DomainError(f"{field}: потрібне число, наприклад 150 або 99,50.")
    return amount


async def undo(session: AsyncSession, error: Exception) -> str:
    """
    A business rule refused the change. The services commit only at the end, so nothing is written, but the loaded
    objects may hold half of the change: the rollback reloads them, and the page shows what is saved.
    """
    await session.rollback()
    return str(error)


def local_path(target: str | None) -> str:
    """Where to go after the login: only a page of this app, never another site (an open redirect)."""
    return target if target and target.startswith("/app/") else "/app/"


class Redirect(Exception):
    """Leave the page: a 303 for the browser, an HX-Redirect header for an HTMX request."""

    def __init__(self, url: str) -> None:
        super().__init__(url)
        self.url = url


class PageForbidden(Exception):
    """Signed in, but the role cannot open this page: a 403 page."""


def _back_to(request: Request) -> str:
    # An HTMX request goes to a fragment URL; after the login the user should come back to the page itself.
    current = request.headers.get("HX-Current-URL")
    url = urlsplit(current) if current else request.url
    return url.path + (f"?{url.query}" if url.query else "")


async def web_user(request: Request, store: UserLoginStoreDep) -> Principal:
    """Like current_principal, but without a session the page sends the user to the login form."""
    try:
        principal = await current_principal(request, store)
    except NotAuthenticated:
        raise Redirect(f"/app/login?next={quote(_back_to(request))}") from None
    if principal.must_change_password and request.url.path != "/app/password":
        raise Redirect("/app/password")  # a temporary password from the admin: change it first
    request.state.web_user = principal
    return principal


WebUser = Annotated[Principal, Depends(web_user)]


def page_roles(*roles: str) -> Callable[[Principal], Awaitable[Principal]]:
    """Router dependency, like require_roles in the API."""

    async def check(user: WebUser) -> Principal:
        if user.role not in roles:
            raise PageForbidden
        return user

    return check
