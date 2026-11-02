"""Coupon admin: the old site's coupons with the products and categories they work on or never work on.

The storefront (OpenCart) applies the coupons; the ERP only keeps them. Python first: the .NET and Java versions have
only birthday coupons so far."""

from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from newerp.application.common import Clock, Dto, LookupItem, Money, NotFoundError
from newerp.domain.common import DomainError
from newerp.domain.marketing import Coupon

LIST_LIMIT = 500


class CouponListItem(Dto):
    id: int
    name: str
    code: str
    type: str  # "P": percent, "F": fixed sum
    discount: Money
    total: Money
    date_start: date | None  # None: no limit (0000-00-00 in the old site's data)
    date_end: date | None
    uses_total: int
    active: bool
    used: int  # how many orders used it


class CouponHistoryRow(Dto):
    order_id: int
    customer_id: int
    amount: Money
    date_added: datetime


class CouponCardDto(Dto):
    id: int
    name: str
    code: str
    type: str
    discount: Money
    total: Money
    logged: bool
    shipping: bool
    date_start: date | None
    date_end: date | None
    uses_total: int
    uses_customer: int
    active: bool
    date_added: datetime
    products: list[LookupItem]
    categories: list[LookupItem]
    excluded_products: list[LookupItem]
    excluded_categories: list[LookupItem]
    history: list[CouponHistoryRow]


class SaveCouponRequest(Dto):
    name: str | None = None
    code: str | None = None
    type: str = "P"
    discount: Decimal | None = None
    total: Decimal = Decimal(0)  # the minimal order sum
    logged: bool = False  # only for a logged-in customer
    shipping: bool = False  # free shipping
    date_start: date | None = None
    date_end: date | None = None
    uses_total: int = 1  # 0: no limit
    uses_customer: int = 1
    active: bool = True
    product_ids: list[int] | None = None
    category_ids: list[int] | None = None
    excluded_product_ids: list[int] | None = None
    excluded_category_ids: list[int] | None = None


class CouponStore(Protocol):
    async def list_coupons(self, search: str, limit: int) -> list[CouponListItem]: ...

    async def get(self, coupon_id: int) -> CouponCardDto | None: ...

    async def code_taken(self, code: str, except_id: int | None) -> bool: ...

    async def missing_products(self, product_ids: list[int]) -> list[int]: ...

    async def missing_categories(self, category_ids: list[int]) -> list[int]: ...

    async def add(self, coupon: Coupon, now: datetime) -> int:
        """Writes the coupon and its four lists, and commits."""
        ...

    async def update(self, coupon_id: int, coupon: Coupon) -> bool:
        """Replaces the coupon and its lists; False if there is no such coupon."""
        ...

    async def used(self, coupon_id: int) -> int: ...

    async def delete(self, coupon_id: int) -> None: ...


class CouponService:
    def __init__(self, store: CouponStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    async def list_coupons(self, search: str | None) -> list[CouponListItem]:
        return await self._store.list_coupons((search or "").strip(), LIST_LIMIT)

    async def get(self, coupon_id: int) -> CouponCardDto:
        card = await self._store.get(coupon_id)
        if card is None:
            raise NotFoundError(f"Купон {coupon_id} не знайдено.")
        return card

    async def create(self, request: SaveCouponRequest) -> CouponCardDto:
        coupon = await self._checked(request, None)
        return await self.get(await self._store.add(coupon, self._clock.now()))

    async def update(self, coupon_id: int, request: SaveCouponRequest) -> CouponCardDto:
        coupon = await self._checked(request, coupon_id)
        if not await self._store.update(coupon_id, coupon):
            raise NotFoundError(f"Купон {coupon_id} не знайдено.")
        return await self.get(coupon_id)

    async def delete(self, coupon_id: int) -> None:
        """A coupon that orders used stays for their history: it can only be turned off."""
        await self.get(coupon_id)
        if await self._store.used(coupon_id):
            raise DomainError("Купон уже використали в замовленнях — його можна лише вимкнути.")
        await self._store.delete(coupon_id)

    async def _checked(self, request: SaveCouponRequest, coupon_id: int | None) -> Coupon:
        coupon = Coupon.of(
            request.name, request.code, request.type, request.discount, request.total, request.logged,
            request.shipping, request.date_start, request.date_end, request.uses_total, request.uses_customer,
            request.active, request.product_ids or [], request.category_ids or [], request.excluded_product_ids or [],
            request.excluded_category_ids or [],
        )  # fmt: skip
        if await self._store.code_taken(coupon.code, coupon_id):
            raise DomainError(f"Купон з кодом {coupon.code} уже є.")
        missing = await self._store.missing_products([*coupon.product_ids, *coupon.excluded_product_ids])
        if missing:
            raise DomainError(f"Товарів {', '.join(map(str, missing))} немає.")
        missing = await self._store.missing_categories([*coupon.category_ids, *coupon.excluded_category_ids])
        if missing:
            raise DomainError(f"Категорій {', '.join(map(str, missing))} немає.")
        return coupon
