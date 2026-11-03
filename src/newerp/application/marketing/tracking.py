"""Marketing tracking codes of the old admin (Python first, item 57): a code in a link (?tracking=CODE) marks the
orders it brings, OpenCart counts the clicks, and here every code shows its orders and their sum."""

import logging
import re
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from pydantic import Field

from newerp.application.common import Clock, ConflictError, Dto, Money, NotFoundError
from newerp.domain.common import DomainError

audit = logging.getLogger("newerp.audit")
_CODE = re.compile(r"[A-Za-z0-9_-]{1,64}")


class TrackingCodeDto(Dto):
    id: int = 0
    name: str = Field(default="", max_length=32)
    description: str = Field(default="", max_length=10_000)
    code: str = Field(default="", max_length=64)
    clicks: int = 0
    orders: int = 0
    revenue: Money = Decimal(0)
    link: str = ""  # the storefront link with the code, when the storefront address is set
    date_added: datetime | None = None


class TrackingStore(Protocol):
    async def codes(self) -> list[TrackingCodeDto]:
        """With clicks, the number of placed orders and their sum."""
        ...

    async def code_owner(self, code: str) -> int | None: ...

    async def save(self, item: TrackingCodeDto, now: datetime) -> int: ...

    async def delete(self, marketing_id: int) -> bool: ...


class TrackingService:
    def __init__(self, store: TrackingStore, clock: Clock, store_url: str) -> None:
        self._store = store
        self._clock = clock
        self._store_url = store_url.strip().rstrip("/")

    async def codes(self) -> list[TrackingCodeDto]:
        items = await self._store.codes()
        if not self._store_url:
            return items
        return [i.model_copy(update={"link": f"{self._store_url}/?tracking={i.code}"}) for i in items]

    async def save(self, marketing_id: int | None, item: TrackingCodeDto) -> TrackingCodeDto:
        if marketing_id is not None and marketing_id not in {c.id for c in await self._store.codes()}:
            raise NotFoundError(f"Коду {marketing_id} немає.")
        name, code = item.name.strip(), item.code.strip()
        if not name:
            raise DomainError("Вкажіть назву кампанії.")
        if not _CODE.fullmatch(code):
            raise DomainError("Код: латинські літери, цифри, - та _, до 64 знаків (він іде в посилання).")
        owner = await self._store.code_owner(code)
        if owner is not None and owner != marketing_id:
            raise ConflictError(f"Код «{code}» вже має кампанія №{owner}.")
        clean = item.model_copy(update={"id": marketing_id or 0, "name": name, "code": code})
        saved = await self._store.save(clean, self._clock.now().replace(microsecond=0))
        return next(c for c in await self.codes() if c.id == saved)

    async def delete(self, marketing_id: int, user_id: int) -> None:
        """Orders keep their marketing_id, so the code's history stays in them."""
        if not await self._store.delete(marketing_id):
            raise NotFoundError(f"Коду {marketing_id} немає.")
        audit.info("tracking code deleted: %s, by user %s", marketing_id, user_id)
