"""What every page shares: the templates, the signed-in user, the menu (like MainLayout.razor + Access.cs)."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote, urlsplit

from fastapi import Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

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


templates.env.filters["money"] = money
templates.env.filters["when"] = when


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
