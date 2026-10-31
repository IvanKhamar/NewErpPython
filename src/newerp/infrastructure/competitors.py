"""Competitors, their product pages and price history (our erp_competitor* tables), and loading their pages."""

import ipaddress
import socket
from collections.abc import Iterable
from decimal import Decimal
from urllib.parse import urljoin, urlsplit

import anyio
import httpcore
import httpx
from sqlalchemy import Boolean, Column, DateTime, Integer, Numeric, String, Table, delete, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.competitors import CompetitorDto, CompetitorLinkDto, CompetitorPriceDto, PageFetchError
from newerp.domain.competitors import Competitor, CompetitorLink, CompetitorPrice, is_on_site, is_public
from newerp.infrastructure.db import Base
from newerp.infrastructure.models.catalog import DdProduct, DdProductDescription

competitors = Table(
    "erp_competitor",
    Base.metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(100)),
    Column("site_url", String(255)),
    Column("price_regex", String(255)),
    Column("active", Boolean),
    Column("created_at", DateTime),
)

links = Table(
    "erp_competitor_link",
    Base.metadata,
    Column("id", Integer, primary_key=True),
    Column("competitor_id", Integer),
    Column("product_id", Integer),
    Column("url", String(500)),
    Column("last_price", Numeric(15, 2)),
    Column("last_checked_at", DateTime),
    Column("last_error", String(255)),
    Column("created_at", DateTime),
)

prices = Table(
    "erp_competitor_price",
    Base.metadata,
    Column("id", Integer, primary_key=True),
    Column("link_id", Integer),
    Column("price", Numeric(15, 2)),
    Column("source", String(10)),
    Column("checked_at", DateTime),
)

Base.registry.map_imperatively(Competitor, competitors, properties={"is_active": competitors.c.active})
Base.registry.map_imperatively(CompetitorLink, links)
Base.registry.map_imperatively(CompetitorPrice, prices)


class SqlCompetitorStore:
    def __init__(self, session: AsyncSession, language_id: int) -> None:
        self._session = session
        self._language_id = language_id

    async def list_competitors(self) -> list[CompetitorDto]:
        return await self._competitors()

    async def get_competitor_dto(self, competitor_id: int) -> CompetitorDto | None:
        found = await self._competitors(competitors.c.id == competitor_id)
        return found[0] if found else None

    async def find_competitor(self, competitor_id: int) -> Competitor | None:
        return await self._session.get(Competitor, competitor_id)

    async def list_links(self, product_id: int | None) -> list[CompetitorLinkDto]:
        return await self._links(*([links.c.product_id == product_id] if product_id is not None else []))

    async def get_link_dto(self, link_id: int) -> CompetitorLinkDto | None:
        found = await self._links(links.c.id == link_id)
        return found[0] if found else None

    async def find_link(self, link_id: int) -> CompetitorLink | None:
        return await self._session.get(CompetitorLink, link_id)

    async def active_link_ids(self) -> list[int]:
        rows = await self._session.scalars(
            select(links.c.id)
            .join(competitors, competitors.c.id == links.c.competitor_id)
            .where(competitors.c.active.is_(True))
            .order_by(links.c.id)
        )
        return list(rows)

    async def link_exists(self, competitor_id: int, product_id: int) -> bool:
        return bool(
            await self._session.scalar(
                select(exists().where(links.c.competitor_id == competitor_id, links.c.product_id == product_id))
            )
        )

    async def product_exists(self, product_id: int) -> bool:
        return bool(await self._session.scalar(select(exists().where(DdProduct.product_id == product_id))))

    async def history(self, link_id: int) -> list[CompetitorPriceDto]:
        rows = await self._session.execute(
            select(prices.c.price, prices.c.source, prices.c.checked_at)
            .where(prices.c.link_id == link_id)
            .order_by(prices.c.checked_at.desc(), prices.c.id.desc())
        )
        return [CompetitorPriceDto(price=price, source=source, checked_at=at) for price, source, at in rows]

    async def our_prices(self, product_ids: list[int]) -> dict[int, Decimal]:
        if not product_ids:
            return {}
        rows = await self._session.execute(
            select(DdProduct.product_id, DdProduct.price).where(DdProduct.product_id.in_(product_ids))
        )
        return {product_id: price for product_id, price in rows}

    def add(self, entity: Competitor | CompetitorLink | CompetitorPrice) -> None:
        self._session.add(entity)

    async def delete_link(self, link_id: int) -> None:
        # InnoDB tables: both deletes go in one transaction.
        await self._session.execute(delete(prices).where(prices.c.link_id == link_id))
        await self._session.execute(delete(links).where(links.c.id == link_id))
        await self._session.commit()

    async def save_changes(self) -> None:
        # Like SaveChanges: new rows get their ids, changed entities are written.
        await self._session.commit()

    async def _competitors(self, *conditions) -> list[CompetitorDto]:
        c = competitors.c
        link_count = select(func.count()).where(links.c.competitor_id == c.id).scalar_subquery()
        rows = await self._session.execute(
            select(c.id, c.name, c.site_url, c.price_regex, c.active, link_count).where(*conditions).order_by(c.name)
        )
        return [
            CompetitorDto(id=i, name=name, site_url=url, price_regex=pattern, is_active=active, links=count)
            for i, name, url, pattern, active, count in rows
        ]

    async def _links(self, *conditions) -> list[CompetitorLinkDto]:
        # The product name comes from its description in the configured language; without one: "Товар N".
        d, link = DdProductDescription, links.c
        product_name = func.coalesce(d.name, func.concat("Товар ", link.product_id))
        rows = await self._session.execute(
            select(
                link.id,
                link.competitor_id,
                competitors.c.name,
                link.product_id,
                product_name,
                link.url,
                link.last_price,
                link.last_checked_at,
                link.last_error,
            )  # fmt: skip
            .join(competitors, competitors.c.id == link.competitor_id)
            .outerjoin(d, (d.product_id == link.product_id) & (d.language_id == self._language_id))
            .where(*conditions)
            .order_by(d.name, competitors.c.name)
        )
        return [
            CompetitorLinkDto(
                id=r[0],
                competitor_id=r[1],
                competitor_name=r[2],
                product_id=r[3],
                product_name=r[4],
                url=r[5],
                last_price=r[6],
                last_checked_at=r[7],
                last_error=r[8],
            )  # fmt: skip
            for r in rows
        ]


# ═══ Loading pages ═══

MAX_REDIRECTS = 3
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 20


class PublicOnlyBackend(httpcore.AsyncNetworkBackend):
    """Connects only to public addresses. The check is made here, at the moment of connecting, so a DNS answer cannot
    change between the check and the request (like ConnectCallback of SocketsHttpHandler in .NET)."""

    def __init__(self) -> None:
        self._inner = httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 - the httpcore interface has it
        local_address: str | None = None,
        socket_options: Iterable | None = None,
    ) -> httpcore.AsyncNetworkStream:
        try:
            answers = await anyio.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as error:
            raise PageFetchError(f"Сайт {host} не знайдено.") from error
        # An IPv6 answer may end with a zone ("fe80::1%eth0"); the zone is not part of the address.
        addresses = [ipaddress.ip_address(str(answer[4][0]).split("%")[0]) for answer in answers]
        if not addresses or not all(is_public(address) for address in addresses):
            raise PageFetchError("Адреса сайту веде в локальну мережу.")
        # TLS still checks the certificate against the host name: httpcore starts TLS with the original host.
        return await self._inner.connect_tcp(
            str(addresses[0]), port, timeout=timeout, local_address=local_address, socket_options=socket_options
        )

    async def connect_unix_socket(self, path: str, timeout: float | None = None, socket_options=None):  # noqa: ASYNC109
        raise PageFetchError("Адреса сайту веде в локальну мережу.")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class PublicOnlyTransport(httpx.AsyncHTTPTransport):
    def __init__(self) -> None:
        super().__init__()
        # httpx has no option for the network backend, so the connection pool is built again with ours.
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(), network_backend=PublicOnlyBackend()
        )


class HttpPageFetcher:
    """Loads a competitor's page. Redirects are followed by hand and only within the competitor's site, so a link
    cannot lead the server to another host. Local network addresses are refused while connecting. The body is cut
    at 2 MB. Use it with `async with`, like an IDisposable with `using`."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        """transport: for tests, a stub without the address check."""
        self._client = httpx.AsyncClient(
            transport=transport or PublicOnlyTransport(),
            timeout=TIMEOUT_SECONDS,
            follow_redirects=False,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; NewErp price check)",
                "Accept": "text/html,application/xhtml+xml",
            },
        )

    async def __aenter__(self) -> "HttpPageFetcher":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self._client.aclose()

    async def fetch(self, page: str, site_host: str) -> str:
        current = page
        for _ in range(MAX_REDIRECTS + 1):
            if not is_on_site(current, site_host):
                raise PageFetchError(f"Сторінка веде на інший сайт: {urlsplit(current).hostname}")
            try:
                async with self._client.stream("GET", current) as response:
                    status = response.status_code
                    if 300 <= status < 400:
                        location = response.headers.get("location")
                        if not location:
                            raise PageFetchError(f"Сайт переадресував без адреси (HTTP {status}).")
                        current = urljoin(current, location)
                        continue
                    if status != 200:
                        raise PageFetchError(f"Сайт відповів HTTP {status}.")
                    return await _read_body(response)
            except httpx.TimeoutException as error:
                raise PageFetchError(f"Сайт не відповів за {TIMEOUT_SECONDS} секунд.") from error
            except httpx.HTTPError as error:
                raise PageFetchError(f"Сайт недоступний: {type(error).__name__}.") from error
        raise PageFetchError("Забагато переадресацій.")


async def _read_body(response: httpx.Response) -> str:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body += chunk
        if len(body) >= MAX_BYTES:
            break
    # Ukrainian sites may be in windows-1251; Python knows that codec without any extra provider.
    try:
        return bytes(body[:MAX_BYTES]).decode(response.charset_encoding or "utf-8", errors="replace")
    except LookupError:
        return bytes(body[:MAX_BYTES]).decode("utf-8", errors="replace")
