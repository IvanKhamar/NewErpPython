"""Size pre-orders, as on the old site: a limit and an expected date per product size; the storefront sells ahead,
and what was sold ahead is regularly taken off the limit."""

from datetime import date
from typing import Protocol

from newerp.application.common import Dto, NotFoundError
from newerp.domain.catalog import SizeAvailability, validate_quota

LIST_LIMIT = 200


class PreOrderRow(Dto):
    """A product size with its storefront stock, pre-order limit and the date it is expected."""

    product_option_value_id: int
    product_id: int
    product_name: str
    model: str
    size: str
    quantity: int
    quota: int
    date_available: date | None
    availability: SizeAvailability


class PreOrderFilter(Dto):
    """search: part of the product name or model; only_with_quota: only sizes with a limit above 0."""

    search: str | None = None
    only_with_quota: bool = False


class UpdatePreOrderRequest(Dto):
    quota: int
    date_available: date | None = None


class PreOrderSettleResult(Dto):
    """In how many sizes what was sold ahead was taken off the limit."""

    sizes: int


class PreOrderStore(Protocol):
    async def list_rows(self, pre_order_filter: PreOrderFilter, limit: int) -> list[PreOrderRow]: ...

    async def update(self, product_option_value_id: int, quota: int, date_available: date | None) -> bool:
        """False when there is no such size."""
        ...

    async def settle(self, product_ids: list[int] | None) -> int:
        """Takes what was sold ahead off the limits (of all sizes or of these products only); returns changed sizes."""
        ...


class PreOrderService:
    def __init__(self, store: PreOrderStore) -> None:
        self._store = store

    async def list_rows(self, pre_order_filter: PreOrderFilter) -> list[PreOrderRow]:
        search = pre_order_filter.search.strip() if pre_order_filter.search else None
        return await self._store.list_rows(pre_order_filter.model_copy(update={"search": search or None}), LIST_LIMIT)

    async def update(self, product_option_value_id: int, request: UpdatePreOrderRequest) -> None:
        validate_quota(request.quota)
        if not await self._store.update(product_option_value_id, request.quota, request.date_available):
            raise NotFoundError(f"Розміру {product_option_value_id} не знайдено.")

    async def settle(self) -> PreOrderSettleResult:
        return PreOrderSettleResult(sizes=await self._store.settle(None))
