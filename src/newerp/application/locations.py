"""Directory of warehouses and pickup points (dd_warehouse) with Excel import and export."""

from collections.abc import Sequence
from typing import Protocol

from newerp.application.common import Dto, NotFoundError, Spreadsheet
from newerp.domain.common import DomainError
from newerp.domain.locations import COLUMNS, ShopLocationData, parse_import, validate

MAX_IMPORT_BYTES = 5 * 1024 * 1024


class LocationDto(Dto):
    id: int
    name: str
    description: str
    contact: str
    telephone: str
    city: str
    address: str
    type: str


class SaveLocationRequest(Dto):
    name: str | None = None
    description: str | None = None
    contact: str | None = None
    telephone: str | None = None
    city: str | None = None
    address: str | None = None
    type: str | None = None


class LocationImportResult(Dto):
    """How many were added and updated, or the errors (then nothing was written)."""

    added: int
    updated: int
    errors: list[str]


class LocationStore(Protocol):
    async def list_all(self, search: str | None) -> list[LocationDto]: ...

    async def get(self, location_id: int) -> LocationDto | None: ...

    async def add(self, data: ShopLocationData) -> int: ...

    async def update(self, location_id: int, data: ShopLocationData) -> bool:
        """False if there is no such record."""
        ...

    async def delete(self, location_id: int) -> bool: ...

    async def existing_ids(self, ids: list[int]) -> set[int]: ...


class StockHolder(Protocol):
    async def holds_stock(self, warehouse_id: int) -> bool:
        """Whether anything lies at the warehouse."""
        ...


class LocationService:
    def __init__(self, store: LocationStore, spreadsheet: Spreadsheet, holders: Sequence[StockHolder]) -> None:
        self._store = store
        self._spreadsheet = spreadsheet
        self._holders = holders  # consumables and sewn pieces at warehouses

    async def list_all(self, search: str | None) -> list[LocationDto]:
        return await self._store.list_all((search or "").strip() or None)

    async def get(self, location_id: int) -> LocationDto:
        location = await self._store.get(location_id)
        if location is None:
            raise NotFoundError(f"Склад {location_id} не знайдено.")
        return location

    async def create(self, request: SaveLocationRequest) -> int:
        return await self._store.add(_validate(request))

    async def update(self, location_id: int, request: SaveLocationRequest) -> None:
        if not await self._store.update(location_id, _validate(request)):
            raise NotFoundError(f"Склад {location_id} не знайдено.")

    async def delete(self, location_id: int) -> None:
        """A warehouse that still holds anything cannot be deleted: its stock would silently move to the main one."""
        for holder in self._holders:
            if await holder.holds_stock(location_id):
                raise DomainError(
                    "На цьому складі ще є розхідники або пошиті вироби. Спершу перемістіть їх на інший склад."
                )
        if not await self._store.delete(location_id):
            raise NotFoundError(f"Склад {location_id} не знайдено.")

    async def export(self) -> bytes:
        rows = [
            [x.id, x.name, x.description, x.contact, x.telephone, x.city, x.address, x.type]
            for x in await self._store.list_all(None)
        ]
        return self._spreadsheet.write("Склади", COLUMNS, rows)

    async def import_file(self, content: bytes) -> LocationImportResult:
        """A row with an existing id updates it, the rest are added. With errors nothing is written."""
        rows, errors = parse_import(self._spreadsheet.read(content))
        if errors:
            return LocationImportResult(added=0, updated=0, errors=errors)

        existing = await self._store.existing_ids([r.id for r in rows if r.id > 0])
        added = updated = 0
        for row in rows:
            if row.id in existing:
                await self._store.update(row.id, row.data)
                updated += 1
            else:
                # An id that is not in the database is not recreated: the record gets a new one, as in the old import.
                await self._store.add(row.data)
                added += 1
        return LocationImportResult(added=added, updated=updated, errors=[])


def _validate(r: SaveLocationRequest) -> ShopLocationData:
    return validate(r.name, r.description, r.contact, r.telephone, r.city, r.address, r.type)
