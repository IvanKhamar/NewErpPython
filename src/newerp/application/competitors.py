"""Competitor price monitoring (like the old CRM's "моніторинг цін конкурентів"): pages of our products on competitors'
sites, a check that reads the price from each page, prices typed in by hand, and our price against theirs."""

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum, auto
from typing import Protocol

from newerp.application.common import Clock, Dto, Money, NotFoundError
from newerp.domain.common import DomainError
from newerp.domain.competitors import AUTO, MANUAL, Competitor, CompetitorLink, CompetitorPrice, extract_price


class CompetitorDto(Dto):
    """A competitor and how many pages of our products we watch there."""

    id: int
    name: str
    site_url: str
    price_regex: str
    is_active: bool
    links: int


class SaveCompetitorRequest(Dto):
    """price_regex is optional, its first group is the price; it is tried before the usual markup."""

    name: str | None = None
    site_url: str | None = None
    price_regex: str | None = None
    is_active: bool = True


class CompetitorLinkDto(Dto):
    """A page of our product at a competitor with the last price; last_error is empty when the last check worked."""

    id: int
    competitor_id: int
    competitor_name: str
    product_id: int
    product_name: str
    url: str
    last_price: Money | None
    last_checked_at: datetime | None
    last_error: str


class AddCompetitorLinkRequest(Dto):
    competitor_id: int
    product_id: int
    url: str | None = None


class CompetitorPriceRequest(Dto):
    """A price typed in for a site that does not let us read it."""

    price: Decimal


class CompetitorPriceDto(Dto):
    price: Money
    source: str
    checked_at: datetime


class CompetitorCheckResult(Dto):
    """Pages checked, in how many the price changed, how many failed (why: in last_error)."""

    checked: int
    changed: int
    failed: int


class CompetitorColumn(Dto):
    id: int
    name: str


class CompetitorReportRow(Dto):
    """prices follow the competitors columns (None: no page or price). difference_percent: how much our price is
    above (+) or below (−) the lowest competitor price."""

    product_id: int
    product_name: str
    our_price: Money
    prices: list[Money | None]
    lowest: Money | None
    difference_percent: Money | None


class CompetitorReportDto(Dto):
    """Our price against the last prices of active competitors, a row for every product with pages."""

    competitors: list[CompetitorColumn]
    rows: list[CompetitorReportRow]


class PageFetchError(Exception):
    """The page could not be loaded; the message explains it for a person and goes into last_error."""


class PageFetcher(Protocol):
    async def fetch(self, page: str, site_host: str) -> str:
        """Loads a competitor's page; redirects only within site_host. Raises PageFetchError when the site is down,
        answers with an error or leads elsewhere."""
        ...


class CompetitorStore(Protocol):
    async def list_competitors(self) -> list[CompetitorDto]: ...

    async def get_competitor_dto(self, competitor_id: int) -> CompetitorDto | None: ...

    async def find_competitor(self, competitor_id: int) -> Competitor | None:
        """A competitor to change."""
        ...

    async def list_links(self, product_id: int | None) -> list[CompetitorLinkDto]:
        """Pages (of one product, when given) by product and competitor."""
        ...

    async def get_link_dto(self, link_id: int) -> CompetitorLinkDto | None: ...

    async def find_link(self, link_id: int) -> CompetitorLink | None:
        """A page to change."""
        ...

    async def active_link_ids(self) -> list[int]:
        """Ids of the pages of active competitors, for checking them all."""
        ...

    async def link_exists(self, competitor_id: int, product_id: int) -> bool: ...

    async def product_exists(self, product_id: int) -> bool: ...

    async def history(self, link_id: int) -> list[CompetitorPriceDto]: ...

    async def our_prices(self, product_ids: list[int]) -> dict[int, Decimal]:
        """Our product prices (dd_product.price)."""
        ...

    def add(self, entity: Competitor | CompetitorLink | CompetitorPrice) -> None: ...

    async def delete_link(self, link_id: int) -> None:
        """Deletes the page with its price history."""
        ...

    async def save_changes(self) -> None: ...


class _Outcome(Enum):
    SAME = auto()
    CHANGED = auto()
    FAILED = auto()


class CompetitorService:
    def __init__(self, store: CompetitorStore, fetcher: PageFetcher, clock: Clock) -> None:
        self._store = store
        self._fetcher = fetcher
        self._clock = clock

    async def competitors(self) -> list[CompetitorDto]:
        return await self._store.list_competitors()

    async def create_competitor(self, request: SaveCompetitorRequest) -> CompetitorDto:
        competitor = Competitor(request.name, request.site_url, request.price_regex, request.is_active, self._now())
        self._store.add(competitor)
        await self._store.save_changes()
        return await self._require_competitor_dto(competitor.id)

    async def update_competitor(self, competitor_id: int, request: SaveCompetitorRequest) -> CompetitorDto:
        competitor = await self._require_competitor(competitor_id)
        competitor.update(request.name, request.site_url, request.price_regex, request.is_active)
        await self._store.save_changes()
        return await self._require_competitor_dto(competitor_id)

    async def links(self, product_id: int | None) -> list[CompetitorLinkDto]:
        return await self._store.list_links(product_id)

    async def add_link(self, request: AddCompetitorLinkRequest) -> CompetitorLinkDto:
        competitor = await self._require_competitor(request.competitor_id)
        if not await self._store.product_exists(request.product_id):
            raise NotFoundError(f"Товар {request.product_id} не знайдено.")
        link = CompetitorLink(competitor, request.product_id, request.url, self._now())
        if await self._store.link_exists(competitor.id, request.product_id):
            raise DomainError(f"Для цього товару в «{competitor.name}» сторінка вже є.")
        self._store.add(link)
        await self._store.save_changes()
        return await self._require_link_dto(link.id)

    async def delete_link(self, link_id: int) -> None:
        await self._require_link(link_id)
        await self._store.delete_link(link_id)

    async def set_price(self, link_id: int, request: CompetitorPriceRequest) -> CompetitorLinkDto:
        link = await self._require_link(link_id)
        if (history := link.record_price(request.price, MANUAL, self._now())) is not None:
            self._store.add(history)
        await self._store.save_changes()
        return await self._require_link_dto(link_id)

    async def history(self, link_id: int) -> list[CompetitorPriceDto]:
        await self._require_link(link_id)
        return await self._store.history(link_id)

    async def check(self, link_id: int) -> CompetitorLinkDto:
        """Reads the price from one page now."""
        await self._check_one(link_id)
        return await self._require_link_dto(link_id)

    async def check_all(self) -> CompetitorCheckResult:
        """Reads the prices of every page of active competitors; one failed page does not stop the rest."""
        ids = await self._store.active_link_ids()
        outcomes = [await self._check_one(link_id) for link_id in ids]
        return CompetitorCheckResult(
            checked=len(ids), changed=outcomes.count(_Outcome.CHANGED), failed=outcomes.count(_Outcome.FAILED)
        )

    async def report(self) -> CompetitorReportDto:
        active = [CompetitorColumn(id=c.id, name=c.name) for c in await self._store.list_competitors() if c.is_active]
        by_product: dict[tuple[int, str], list[CompetitorLinkDto]] = {}
        for link in await self._store.list_links(None):
            by_product.setdefault((link.product_id, link.product_name), []).append(link)
        ours = await self._store.our_prices([product_id for product_id, _ in by_product])

        rows = []
        for (product_id, product_name), links in by_product.items():
            prices = [next((link.last_price for link in links if link.competitor_id == c.id), None) for c in active]
            lowest = min((p for p in prices if p is not None), default=None)
            our = ours.get(product_id, Decimal(0))
            difference = (
                ((our - lowest) * 100 / lowest).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
                if lowest is not None and our > 0
                else None
            )
            rows.append(
                CompetitorReportRow(
                    product_id=product_id,
                    product_name=product_name,
                    our_price=our,
                    prices=prices,
                    lowest=lowest,
                    difference_percent=difference,
                )  # fmt: skip
            )
        rows.sort(key=lambda r: r.product_name)
        return CompetitorReportDto(competitors=active, rows=rows)

    async def _check_one(self, link_id: int) -> _Outcome:
        link = await self._require_link(link_id)
        competitor = await self._require_competitor(link.competitor_id)
        before = link.last_price
        try:
            html = await self._fetcher.fetch(link.url, competitor.host)
            found = extract_price(html, competitor.price_regex)
            if found is not None:
                price = found.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                if (history := link.record_price(price, AUTO, self._now())) is not None:
                    self._store.add(history)
                outcome = _Outcome.SAME if before == price else _Outcome.CHANGED
            else:
                link.record_failure("Ціну на сторінці не знайдено.", self._now())
                outcome = _Outcome.FAILED
        except PageFetchError as error:
            link.record_failure(str(error), self._now())
            outcome = _Outcome.FAILED
        # Every page is saved at once: sites answer slowly, and what is done must not be lost because of the next one.
        await self._store.save_changes()
        return outcome

    async def _require_competitor(self, competitor_id: int) -> Competitor:
        competitor = await self._store.find_competitor(competitor_id)
        if competitor is None:
            raise NotFoundError(f"Конкурента {competitor_id} не знайдено.")
        return competitor

    async def _require_competitor_dto(self, competitor_id: int) -> CompetitorDto:
        dto = await self._store.get_competitor_dto(competitor_id)
        if dto is None:
            raise NotFoundError(f"Конкурента {competitor_id} не знайдено.")
        return dto

    async def _require_link(self, link_id: int) -> CompetitorLink:
        link = await self._store.find_link(link_id)
        if link is None:
            raise NotFoundError(f"Сторінку {link_id} не знайдено.")
        return link

    async def _require_link_dto(self, link_id: int) -> CompetitorLinkDto:
        dto = await self._store.get_link_dto(link_id)
        if dto is None:
            raise NotFoundError(f"Сторінку {link_id} не знайдено.")
        return dto

    def _now(self) -> datetime:
        return self._clock.now()
