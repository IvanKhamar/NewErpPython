"""Building blocks shared by all application modules."""

import math
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, PlainSerializer, computed_field
from pydantic.alias_generators import to_camel


class NotFoundError(Exception):
    """The requested record does not exist. The API turns it into 404."""


class ConflictError(Exception):
    """Someone else changed the data in the meantime. The API turns it into 409."""


class Dto(BaseModel):
    """Base for request and response models: camelCase JSON like System.Text.Json in .NET."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


# .NET writes decimal as a JSON number; pydantic would write a string.
Money = Annotated[Decimal, PlainSerializer(float, return_type=float, when_used="json")]


class LookupItem(Dto):
    """A lookup entry: status, source, manager."""

    id: int
    name: str


class PagedResult[T](Dto):
    items: list[T]
    page: int
    page_size: int
    total_count: int

    @computed_field
    @property
    def total_pages(self) -> int:
        return math.ceil(self.total_count / self.page_size) if self.page_size else 0


MAX_PAGE_SIZE = 100


def normalize_paging(page: int, page_size: int) -> tuple[int, int]:
    """Keeps page and size within limits, so a client cannot ask for a million rows."""
    return max(page, 1), min(max(page_size, 1), MAX_PAGE_SIZE)


_UKRAINIAN = "абвгґдеєжзиіїйклмнопрстуфхцчшщьюя"
_UKRAINIAN_ORDER = {letter: index for index, letter in enumerate(_UKRAINIAN)}


def ukrainian_key(text: str) -> tuple[tuple[int, int | str], ...]:
    """Sort key in Ukrainian alphabetical order (є, і, ї, ґ in their places), ignoring case like .NET uk-UA."""
    # Spaces, digits and Latin letters come before Cyrillic, as in .NET.
    return tuple((1, _UKRAINIAN_ORDER[c]) if c in _UKRAINIAN_ORDER else (0, c) for c in text.casefold())


class Clock(Protocol):
    """Current time (like TimeProvider in .NET); tests pass a fixed clock."""

    def now(self) -> datetime:
        """Local wall-clock time without a time zone, as the old site stores it."""
        ...

    def utc_now(self) -> datetime: ...


KYIV = ZoneInfo("Europe/Kyiv")


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(KYIV).replace(tzinfo=None)

    def utc_now(self) -> datetime:
        return datetime.now(UTC)
