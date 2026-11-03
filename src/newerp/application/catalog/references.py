"""Catalog reference lists of the old admin (Python first, item 49).

Filters, attributes and options are all two levels in OpenCart: a group ("Колір", "Склад", "Розмір") with its values
("Червоний", "Льон 100%", "M"). One service serves the three, and manufacturers and downloads are flat lists. A value
or a group that products still use cannot be deleted: the storefront would show broken filters and sizes."""

import logging
import re
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import Field

from newerp.application.common import Dto, NotFoundError
from newerp.domain.catalog import image_path
from newerp.domain.common import DomainError

audit = logging.getLogger("newerp.audit")

_DOWNLOAD_FILE = re.compile(r"[\w\-. ]{1,160}")  # a file name in OpenCart's storage/download, no folders


class ReferenceKind(StrEnum):
    FILTERS = "filters"
    ATTRIBUTES = "attributes"
    OPTIONS = "options"


class ReferenceItemDto(Dto):
    id: int = 0
    name: str = Field(default="", max_length=128)
    sort_order: int = 0
    image: str = Field(default="", max_length=255)  # option values only
    products: int = 0  # how many products use it (read only)


class ReferenceGroupDto(Dto):
    id: int = 0
    name: str = Field(default="", max_length=128)
    sort_order: int = 0
    type: Literal["select", "radio", "checkbox", "text", "textarea", "date"] = "select"  # options only
    items: list[ReferenceItemDto] = Field(default_factory=list)


class ManufacturerDto(Dto):
    id: int = 0
    name: str = Field(default="", max_length=64)
    image: str = Field(default="", max_length=255)
    sort_order: int = 0
    products: int = 0


class DownloadDto(Dto):
    """A file the customer gets after buying; the file itself lies in the storefront's storage/download."""

    id: int = 0
    name: str = Field(default="", max_length=64)
    filename: str = Field(default="", max_length=160)
    mask: str = Field(default="", max_length=128)  # the name the customer sees
    products: int = 0


class ReferenceStore(Protocol):
    async def groups(self, kind: ReferenceKind) -> list[ReferenceGroupDto]: ...

    async def save_group(self, kind: ReferenceKind, group: ReferenceGroupDto) -> int: ...

    async def save_item(self, kind: ReferenceKind, group_id: int, item: ReferenceItemDto) -> int: ...

    async def group_of(self, kind: ReferenceKind, item_id: int) -> int | None: ...

    async def delete_group(self, kind: ReferenceKind, group_id: int) -> None:
        """The group with its values."""
        ...

    async def delete_item(self, kind: ReferenceKind, item_id: int) -> None: ...

    async def manufacturers(self) -> list[ManufacturerDto]: ...

    async def save_manufacturer(self, manufacturer: ManufacturerDto) -> int: ...

    async def delete_manufacturer(self, manufacturer_id: int) -> None: ...

    async def downloads(self) -> list[DownloadDto]: ...

    async def save_download(self, download: DownloadDto) -> int: ...

    async def delete_download(self, download_id: int) -> None: ...


class CatalogReferenceService:
    def __init__(self, store: ReferenceStore) -> None:
        self._store = store

    # Two-level lists

    async def groups(self, kind: ReferenceKind) -> list[ReferenceGroupDto]:
        return await self._store.groups(kind)

    async def save_group(
        self, kind: ReferenceKind, group_id: int | None, group: ReferenceGroupDto
    ) -> ReferenceGroupDto:
        name = _name(group.name)
        if group_id is not None:
            await self._group(kind, group_id)
        saved = await self._store.save_group(kind, group.model_copy(update={"id": group_id or 0, "name": name}))
        return await self._group(kind, saved)

    async def save_item(
        self, kind: ReferenceKind, group_id: int, item_id: int | None, item: ReferenceItemDto
    ) -> ReferenceGroupDto:
        await self._group(kind, group_id)
        if item_id is not None and await self._store.group_of(kind, item_id) != group_id:
            raise NotFoundError(f"Значення {item_id} у групі {group_id} немає.")
        image = image_path(item.image) if kind == ReferenceKind.OPTIONS else ""
        clean = item.model_copy(update={"id": item_id or 0, "name": _name(item.name), "image": image})
        await self._store.save_item(kind, group_id, clean)
        return await self._group(kind, group_id)

    async def delete_group(self, kind: ReferenceKind, group_id: int, user_id: int) -> None:
        group = await self._group(kind, group_id)
        used = [item.name for item in group.items if item.products]
        if used:
            raise DomainError(f"Значення групи «{group.name}» є в товарах: {', '.join(used)}. Спершу приберіть їх.")
        await self._store.delete_group(kind, group_id)
        audit.info("%s group deleted: %s, by user %s", kind, group_id, user_id)

    async def delete_item(self, kind: ReferenceKind, item_id: int, user_id: int) -> None:
        group_id = await self._store.group_of(kind, item_id)
        if group_id is None:
            raise NotFoundError(f"Значення {item_id} немає.")
        item = next(i for i in (await self._group(kind, group_id)).items if i.id == item_id)
        if item.products:
            raise DomainError(f"«{item.name}» є в {item.products} товарах: спершу приберіть його з них.")
        await self._store.delete_item(kind, item_id)
        audit.info("%s value deleted: %s, by user %s", kind, item_id, user_id)

    async def _group(self, kind: ReferenceKind, group_id: int) -> ReferenceGroupDto:
        group = next((g for g in await self._store.groups(kind) if g.id == group_id), None)
        if group is None:
            raise NotFoundError(f"Групи {group_id} немає.")
        return group

    # Manufacturers

    async def manufacturers(self) -> list[ManufacturerDto]:
        return await self._store.manufacturers()

    async def save_manufacturer(self, manufacturer_id: int | None, manufacturer: ManufacturerDto) -> ManufacturerDto:
        known = {m.id for m in await self._store.manufacturers()}
        if manufacturer_id is not None and manufacturer_id not in known:
            raise NotFoundError(f"Виробника {manufacturer_id} немає.")
        clean = manufacturer.model_copy(
            update={
                "id": manufacturer_id or 0,
                "name": _name(manufacturer.name),
                "image": image_path(manufacturer.image),
            }  # fmt: skip
        )
        saved = await self._store.save_manufacturer(clean)
        return next(m for m in await self._store.manufacturers() if m.id == saved)

    async def delete_manufacturer(self, manufacturer_id: int, user_id: int) -> None:
        manufacturer = next((m for m in await self._store.manufacturers() if m.id == manufacturer_id), None)
        if manufacturer is None:
            raise NotFoundError(f"Виробника {manufacturer_id} немає.")
        if manufacturer.products:
            raise DomainError(f"Виробник «{manufacturer.name}» є в {manufacturer.products} товарах.")
        await self._store.delete_manufacturer(manufacturer_id)
        audit.info("manufacturer deleted: %s, by user %s", manufacturer_id, user_id)

    # Downloads

    async def downloads(self) -> list[DownloadDto]:
        return await self._store.downloads()

    async def save_download(self, download_id: int | None, download: DownloadDto) -> DownloadDto:
        if download_id is not None and download_id not in {d.id for d in await self._store.downloads()}:
            raise NotFoundError(f"Завантаження {download_id} немає.")
        filename = download.filename.strip()
        if not _DOWNLOAD_FILE.fullmatch(filename) or ".." in filename:
            raise DomainError("Ім'я файлу: лише літери, цифри, - _ . і пробіл, без папок.")
        clean = download.model_copy(
            update={
                "id": download_id or 0,
                "name": _name(download.name),
                "filename": filename,
                "mask": download.mask.strip() or filename,
            }  # fmt: skip
        )
        saved = await self._store.save_download(clean)
        return next(d for d in await self._store.downloads() if d.id == saved)

    async def delete_download(self, download_id: int, user_id: int) -> None:
        download = next((d for d in await self._store.downloads() if d.id == download_id), None)
        if download is None:
            raise NotFoundError(f"Завантаження {download_id} немає.")
        if download.products:
            raise DomainError(f"Завантаження «{download.name}» прив'язане до {download.products} товарів.")
        await self._store.delete_download(download_id)
        audit.info("download deleted: %s, by user %s", download_id, user_id)


def _name(value: str) -> str:
    name = value.strip()
    if not name:
        raise DomainError("Вкажіть назву.")
    return name
