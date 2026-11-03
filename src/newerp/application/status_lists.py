"""The status lists of the old admin (Python first, item 69): order statuses (with the old site's colour), return
statuses, reasons and actions, stock statuses. One service serves the five, as OpenCart keeps them alike: an id and a
name per language. A status orders, returns or products still have cannot be deleted."""

import logging
import re
from typing import Literal, Protocol

from pydantic import Field

from newerp.application.common import Dto, NotFoundError
from newerp.domain.common import DomainError

audit = logging.getLogger("newerp.audit")
_COLOR = re.compile(r"#[0-9a-fA-F]{6}")

StatusList = Literal["order-statuses", "return-statuses", "return-reasons", "return-actions", "stock-statuses"]


class StatusItemDto(Dto):
    id: int = 0
    name: str = Field(default="", max_length=32)
    color: str = Field(default="", max_length=7)  # order statuses only: #rrggbb or empty
    used: int = 0  # orders, returns or products that have it


class StatusListStore(Protocol):
    async def items(self, kind: StatusList) -> list[StatusItemDto]: ...

    async def save(self, kind: StatusList, item: StatusItemDto) -> int: ...

    async def delete(self, kind: StatusList, item_id: int) -> None: ...


class StatusListService:
    def __init__(self, store: StatusListStore) -> None:
        self._store = store

    async def items(self, kind: StatusList) -> list[StatusItemDto]:
        return await self._store.items(kind)

    async def save(self, kind: StatusList, item_id: int | None, item: StatusItemDto, user_id: int) -> StatusItemDto:
        if item_id is not None and item_id not in {i.id for i in await self._store.items(kind)}:
            raise NotFoundError(f"Запису {item_id} немає.")
        name = item.name.strip()
        if not name:
            raise DomainError("Вкажіть назву.")
        color = item.color.strip() if kind == "order-statuses" else ""
        if color and not _COLOR.fullmatch(color):
            raise DomainError("Колір має бути на кшталт #2e7d32.")  # it goes into a style attribute
        saved = await self._store.save(kind, item.model_copy(update={"id": item_id or 0, "name": name, "color": color}))
        audit.info("%s saved: %s, by user %s", kind, saved, user_id)
        return next(i for i in await self._store.items(kind) if i.id == saved)

    async def delete(self, kind: StatusList, item_id: int, user_id: int) -> None:
        item = next((i for i in await self._store.items(kind) if i.id == item_id), None)
        if item is None:
            raise NotFoundError(f"Запису {item_id} немає.")
        if item.used:
            raise DomainError(f"«{item.name}» мають {item.used} записів: видалити не можна, лише перейменувати.")
        await self._store.delete(kind, item_id)
        audit.info("%s deleted: %s, by user %s", kind, item_id, user_id)
