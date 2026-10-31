"""Shop products, like "Каталог → Товари" of the old site: the ERP changes price, enabled, labels and categories.

New products, descriptions, pictures and SEO addresses stay in the OpenCart admin; stock and order belong to production
and "Порядок товарів".
"""

from datetime import datetime
from decimal import Decimal
from typing import Protocol

from newerp.application.common import Clock, Dto, LookupItem, Money, NotFoundError, PagedResult, normalize_paging
from newerp.domain.catalog import ProductChange
from newerp.domain.common import DomainError


class ProductFilter(Dto):
    """search: part of the name or model, or the product number."""

    search: str | None = None
    category_id: int | None = None
    label_id: int | None = None
    enabled: bool | None = None
    page: int = 1
    page_size: int = 20


class ProductListItem(Dto):
    """quantity is the storefront stock; production fills it from the sewn stock."""

    id: int
    name: str
    model: str
    price: Money
    enabled: bool
    quantity: int
    image: str | None


class ProductSize(Dto):
    """A size with its stock (read only: production keeps the stock)."""

    option_value_id: int
    name: str
    quantity: int


class ProductCardDto(Dto):
    """date_modified is dd_product.date_modified; OpenCart sets it on every save too, so an edit checks it."""

    id: int
    name: str
    model: str
    price: Money
    enabled: bool
    quantity: int
    image: str | None
    date_modified: datetime
    sizes: list[ProductSize]
    labels: list[LookupItem]
    categories: list[LookupItem]


class UpdateProductRequest(Dto):
    """PATCH: None keeps the value. date_modified is the one the card had when opened; a newer product is a 409."""

    price: Decimal | None = None
    enabled: bool | None = None
    label_ids: list[int] | None = None
    category_ids: list[int] | None = None
    date_modified: datetime | None = None


class ProductStore(Protocol):
    async def list_page(self, product_filter: ProductFilter) -> PagedResult[ProductListItem]: ...

    async def get(self, product_id: int) -> ProductCardDto | None: ...

    async def labels(self) -> list[LookupItem]:
        """All labels ("Новинка", "Хіт"…) in their order."""
        ...

    async def categories(self) -> list[LookupItem]:
        """Enabled categories by name."""
        ...

    async def update(
        self, product_id: int, expected: datetime, price: Decimal | None, enabled: bool | None, now: datetime
    ) -> None:
        """Sets price and state (None keeps it) and date_modified = now only while date_modified still equals
        expected; otherwise (changed or deleted) raises ConflictError."""
        ...

    async def replace_labels(self, product_id: int, label_ids: list[int]) -> None: ...

    async def replace_categories(self, product_id: int, category_ids: list[int]) -> None: ...


class ProductService:
    def __init__(self, store: ProductStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    async def list_page(self, product_filter: ProductFilter) -> PagedResult[ProductListItem]:
        page, page_size = normalize_paging(product_filter.page, product_filter.page_size)
        search = product_filter.search.strip() if product_filter.search else None
        return await self._store.list_page(
            product_filter.model_copy(update={"search": search, "page": page, "page_size": page_size})
        )

    async def get(self, product_id: int) -> ProductCardDto:
        card = await self._store.get(product_id)
        if card is None:
            raise NotFoundError(f"Товар {product_id} не знайдено.")
        return card

    async def labels(self) -> list[LookupItem]:
        return await self._store.labels()

    async def categories(self) -> list[LookupItem]:
        return await self._store.categories()

    async def update(self, product_id: int, request: UpdateProductRequest) -> ProductCardDto:
        """OpenCart tables are MyISAM, without transactions, so nothing rolls back: first the guarded UPDATE of the
        product, and labels and categories only after it."""
        current = await self.get(product_id)
        if request.date_modified is None:
            raise DomainError("Не вказано dateModified: з якою версією товару ви працювали.")
        known_labels = {label.id for label in await self._store.labels()}
        # A category disabled later may stay in the product, but it cannot be chosen again.
        known_categories = {c.id for c in await self._store.categories()} | {c.id for c in current.categories}
        change = ProductChange.of(
            request.price, request.enabled, request.label_ids, request.category_ids, known_labels, known_categories
        )
        # date_modified is a datetime without fractions of a second, as OpenCart writes it.
        now = self._clock.now().replace(microsecond=0)
        await self._store.update(product_id, request.date_modified, change.price, change.enabled, now)
        if change.label_ids is not None:
            await self._store.replace_labels(product_id, change.label_ids)
        if change.category_ids is not None:
            await self._store.replace_categories(product_id, change.category_ids)
        return await self.get(product_id)
