"""Competitor price monitoring: competitors, pages of our products on their sites, and reading a price from a page."""

import ipaddress
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import SplitResult, urlsplit, urlunsplit

import regex

from newerp.domain.common import DomainError

NAME_MAX_LENGTH = 100
SITE_URL_MAX_LENGTH = 255
PAGE_URL_MAX_LENGTH = 500
REGEX_MAX_LENGTH = 255
ERROR_MAX_LENGTH = 255

# A competitor's pattern is written by people and the page by a foreign site: the time limit keeps a bad pattern
# from hanging (the built-in re module has no timeout, the regex package has).
REGEX_TIMEOUT_SECONDS = 2


# ═══ Addresses ═══


def require_url(value: str | None, field: str, max_length: int) -> SplitResult:
    """A trimmed absolute http(s) address with a host, not longer than max_length."""
    trimmed = (value or "").strip()
    if not 0 < len(trimmed) <= max_length:
        raise DomainError(f"{field}: вкажіть адресу до {max_length} символів.")
    try:
        parts = urlsplit(trimmed)
        host = parts.hostname
    except ValueError:
        parts, host = None, None
    if parts is None or not parts.scheme or not parts.netloc:
        raise DomainError(f"{field}: це не схоже на адресу сторінки.")
    if parts.scheme.lower() not in ("http", "https") or not host:
        raise DomainError(f"{field}: адреса має починатися з http:// або https://.")
    return parts


def url_text(parts: SplitResult) -> str:
    """The address as .NET Uri writes it: scheme and host in lower case, "/" for an empty path."""
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, parts.fragment))


def host_of(url: str | SplitResult) -> str:
    """The host in lower case without "www.": shop.ua for https://www.Shop.ua/catalog."""
    host = (urlsplit(url) if isinstance(url, str) else url).hostname or ""
    return host.removeprefix("www.")


def is_on_site(page: str | SplitResult, site_host: str) -> bool:
    """Whether the page is on the site: the same host or its subdomain (m.shop.ua on shop.ua)."""
    host = host_of(page)
    return host == site_host or host.endswith("." + site_host)


def is_public(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Whether the address is on the internet, not in a local or service network."""
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    if address.is_loopback or address.is_unspecified or address.is_link_local or address.is_multicast:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        return not (address.is_site_local or address in ipaddress.ip_network("fc00::/7"))
    first, second = address.packed[0], address.packed[1]
    return not (
        first in (0, 10)
        or first >= 224  # multicast and reserved
        or (first == 100 and 64 <= second < 128)  # 100.64/10, shared by providers
        or (first == 172 and 16 <= second < 32)  # 172.16/12
        or (first == 192 and second == 168)  # 192.168/16
    )


# ═══ Prices on a page ═══

_STANDARD = [
    regex.compile(r"""property=["']product:price:amount["'][^>]*content=["']([^"']+)["']""", regex.IGNORECASE),
    regex.compile(r"""content=["']([^"']+)["'][^>]*property=["']product:price:amount["']""", regex.IGNORECASE),
    regex.compile(r"""itemprop=["']price["'][^>]*content=["']([^"']+)["']""", regex.IGNORECASE),
    regex.compile(r"""content=["']([^"']+)["'][^>]*itemprop=["']price["']""", regex.IGNORECASE),
    regex.compile(r""""price"\s*:\s*"?([0-9][0-9\s.,]*)"""),
]
# \s in a str pattern also matches the no-break spaces shops put between thousands.
_NUMBER = regex.compile(r"[0-9][0-9\s.,]*")


def require_regex(pattern: str | None) -> str:
    """Checks a competitor's pattern: it must compile and have a group for the price. Empty: no own pattern."""
    trimmed = (pattern or "").strip()
    if not trimmed:
        return ""
    if len(trimmed) > REGEX_MAX_LENGTH:
        raise DomainError(f"Вираз для ціни — не довше {REGEX_MAX_LENGTH} символів.")
    try:
        compiled = regex.compile(trimmed)
    except regex.error as error:
        raise DomainError(f"Вираз для ціни з помилкою: {error}") from error
    if compiled.groups < 1:
        raise DomainError('Вираз для ціни має містити групу в дужках, наприклад class="price">([0-9 ]+).')
    return trimmed


def extract_price(html: str | None, custom_pattern: str | None) -> Decimal | None:
    """The product price on a shop page. First the competitor's own pattern (when set), then the usual markup:
    the product:price:amount meta tag, an itemprop="price" element, "price" in schema.org JSON-LD."""
    if not html:
        return None
    if custom_pattern and custom_pattern.strip():
        custom = regex.compile(custom_pattern, regex.IGNORECASE | regex.DOTALL)
        if (price := _first(custom, html)) is not None:
            return price
    for pattern in _STANDARD:
        if (price := _first(pattern, html)) is not None:
            return price
    return None


def _first(pattern: regex.Pattern, html: str) -> Decimal | None:
    try:
        for match in pattern.finditer(html, timeout=REGEX_TIMEOUT_SECONDS):
            if (price := parse_price(match.group(1))) is not None:
                return price
    except TimeoutError:
        pass  # the pattern ran out of time: no price from it, the next one is tried
    return None


def parse_price(text: str | None) -> Decimal | None:
    """ "1 299,00", "1,299.00", "1299.5" or "1 299 грн" as a number: spaces split thousands; of a comma and a dot
    the last one is the decimal point; a single comma or dot with three digits after it splits thousands."""
    if text is None:
        return None
    match = _NUMBER.search(text)
    if match is None:
        return None
    s = regex.sub(r"\s", "", match.group()).rstrip(".,")
    comma, dot = s.rfind(","), s.rfind(".")
    point = max(comma, dot)
    if point >= 0:
        whole = s[:point].replace(",", "").replace(".", "")
        fraction = s[point + 1 :]
        only_separator = (comma < 0 or dot < 0) and len(fraction) == 3 and s.index(s[point]) == point and whole
        s = whole + fraction if only_separator else f"{whole}.{fraction}"
    try:
        price = Decimal(s)
    except InvalidOperation:
        return None
    return price if price > 0 else None


# ═══ Entities ═══

AUTO = "auto"  # the price was read from the page
MANUAL = "manual"  # the price was typed in (the site does not let us read it)


class Competitor:
    """A competitor whose prices we watch (our table erp_competitor). site_url is the site's home page."""

    def __init__(
        self, name: str | None, site_url: str | None, price_regex: str | None, is_active: bool, now: datetime
    ) -> None:
        self.id: int | None = None
        self.update(name, site_url, price_regex, is_active)
        self.created_at = now

    @property
    def host(self) -> str:
        """Product pages must be on this host (without "www.")."""
        return host_of(self.site_url)

    def update(self, name: str | None, site_url: str | None, price_regex: str | None, is_active: bool) -> None:
        trimmed = (name or "").strip()
        if not 0 < len(trimmed) <= NAME_MAX_LENGTH:
            raise DomainError(f"Назва конкурента: від 1 до {NAME_MAX_LENGTH} символів.")
        self.name = trimmed
        self.site_url = url_text(require_url(site_url, "Сайт", SITE_URL_MAX_LENGTH))
        self.price_regex = require_regex(price_regex)
        self.is_active = is_active


class CompetitorPrice:
    """A price history row (erp_competitor_price): it appears when the price changed. source is auto or manual."""

    def __init__(self, link_id: int, price: Decimal, source: str, checked_at: datetime) -> None:
        self.id: int | None = None
        self.link_id = link_id
        self.price = price
        self.source = source
        self.checked_at = checked_at


class CompetitorLink:
    """A page of our product (dd_product) on a competitor's site with the last price found (erp_competitor_link).
    last_error says why the last check failed; empty: it worked."""

    def __init__(self, competitor: Competitor, product_id: int, url: str | None, now: datetime) -> None:
        page = require_url(url, "Сторінка товару", PAGE_URL_MAX_LENGTH)
        if not is_on_site(page, competitor.host):
            raise DomainError(f"Сторінка має бути на сайті «{competitor.name}» ({competitor.host}).")
        self.id: int | None = None
        self.competitor_id = competitor.id
        self.product_id = product_id
        self.url = url_text(page)
        self.last_price: Decimal | None = None
        self.last_checked_at: datetime | None = None
        self.last_error = ""
        self.created_at = now

    def record_price(self, price: Decimal, source: str, now: datetime) -> CompetitorPrice | None:
        """A price found or typed in. The history gets a row only when the price changed (or is found the first
        time): then that row is returned, else None."""
        if price <= 0 or round(price, 2) != price:
            raise DomainError("Ціна має бути більшою за нуль, не більше двох знаків після коми.")
        changed = self.last_price != price
        self.last_price = price
        self.last_checked_at = now
        self.last_error = ""
        return CompetitorPrice(self.id, price, source, now) if changed else None

    def record_failure(self, error: str, now: datetime) -> None:
        """The check failed: the last price stays, the reason goes into last_error."""
        self.last_checked_at = now
        self.last_error = error[:ERROR_MAX_LENGTH]
