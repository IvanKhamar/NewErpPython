"""Filters, attributes, options, manufacturers and downloads over the OpenCart tables.

The three two-level lists differ only in table and column names, so each is described once by a Spec and served by
the same code: a group table with its names per language, and a value table with its names per language."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Table, delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.catalog.references import (
    DownloadDto,
    ManufacturerDto,
    ReferenceGroupDto,
    ReferenceItemDto,
    ReferenceKind,
)
from newerp.infrastructure.catalog.dictionaries import category_filter
from newerp.infrastructure.catalog.product_edit import STORE_ID, product_option, seo_url
from newerp.infrastructure.catalog.tables import (
    attribute,
    attribute_description,
    attribute_group,
    attribute_group_description,
    download,
    download_description,
    filter_group,
    filter_group_description,
    manufacturer,
    manufacturer_to_store,
    option,
    option_value,
    product_attribute,
    product_to_download,
)
from newerp.infrastructure.models.catalog import (
    DdFilter,
    DdFilterDescription,
    DdOptionDescription,
    DdOptionValueDescription,
    DdProduct,
    DdProductFilter,
    DdProductOptionValue,
)


@dataclass(frozen=True)
class Spec:
    group: Table
    group_names: Table
    item: Table
    item_names: Table
    group_key: str  # the group id column, the same in the value table
    item_key: str
    item_names_group: bool  # whether the value's names table repeats the group id (filters and options do)
    used_in: tuple  # (table, value id column) pairs that make a value "used"


def _spec(kind: ReferenceKind) -> Spec:
    if kind == ReferenceKind.FILTERS:
        return Spec(filter_group, filter_group_description, DdFilter.__table__, DdFilterDescription.__table__,
                    "filter_group_id", "filter_id", True,
                    ((DdProductFilter.__table__, "filter_id"), (category_filter, "filter_id")))  # fmt: skip
    if kind == ReferenceKind.ATTRIBUTES:
        return Spec(attribute_group, attribute_group_description, attribute, attribute_description,
                    "attribute_group_id", "attribute_id", False, ((product_attribute, "attribute_id"),))  # fmt: skip
    return Spec(option, DdOptionDescription.__table__, option_value, DdOptionValueDescription.__table__,
                "option_id", "option_value_id", True,
                ((DdProductOptionValue.__table__, "option_value_id"),))  # fmt: skip


class SqlReferenceStore:
    def __init__(self, session: AsyncSession, language_id: int) -> None:
        self._session = session
        self._language_id = language_id

    async def groups(self, kind: ReferenceKind) -> list[ReferenceGroupDto]:
        s, lang = _spec(kind), self._language_id
        g, gn = s.group.c, s.group_names.c
        group_rows = await self._session.execute(
            select(g[s.group_key], func.coalesce(gn.name, ""), g.sort_order, *([g.type] if "type" in g else []))
            .outerjoin(s.group_names, (gn[s.group_key] == g[s.group_key]) & (gn.language_id == lang))
            .order_by(g.sort_order, gn.name)
        )
        i, n = s.item.c, s.item_names.c
        used = [
            select(func.count()).where(table.c[column] == i[s.item_key]).scalar_subquery()
            for table, column in s.used_in
        ]
        item_rows = await self._session.execute(
            select(i[s.item_key], i[s.group_key], func.coalesce(n.name, ""), i.sort_order,
                   *([i.image] if "image" in i else []), sum(used[1:], used[0]))
            .outerjoin(s.item_names, (n[s.item_key] == i[s.item_key]) & (n.language_id == lang))
            .order_by(i.sort_order, n.name)
        )  # fmt: skip
        items: dict[int, list[ReferenceItemDto]] = {}
        for r in item_rows:
            image = r[4] if "image" in i else ""
            items.setdefault(r[1], []).append(
                ReferenceItemDto(id=r[0], name=r[2], sort_order=r[3], image=image or "", products=r[-1])
            )
        return [
            ReferenceGroupDto(
                id=r[0], name=r[1], sort_order=r[2], type=r[3] if len(r) > 3 else "select", items=items.get(r[0], [])
            )  # fmt: skip
            for r in group_rows
        ]

    async def save_group(self, kind: ReferenceKind, group: ReferenceGroupDto) -> int:
        s, session = _spec(kind), self._session
        values: dict[str, object] = {"sort_order": group.sort_order}
        if kind == ReferenceKind.OPTIONS:
            values["type"] = group.type
        if group.id:
            await session.execute(update(s.group).where(s.group.c[s.group_key] == group.id).values(values))
            group_id = group.id
        else:
            group_id = (await session.execute(insert(s.group).values(values))).inserted_primary_key[0]
        await self._replace_name(s.group_names, s.group_key, group_id, group.name)
        await session.commit()
        return group_id

    async def save_item(self, kind: ReferenceKind, group_id: int, item: ReferenceItemDto) -> int:
        s, session = _spec(kind), self._session
        values: dict[str, object] = {"sort_order": item.sort_order, s.group_key: group_id}
        if kind == ReferenceKind.OPTIONS:
            values["image"] = item.image
        if item.id:
            await session.execute(update(s.item).where(s.item.c[s.item_key] == item.id).values(values))
            item_id = item.id
        else:
            item_id = (await session.execute(insert(s.item).values(values))).inserted_primary_key[0]
        extra = {s.group_key: group_id} if s.item_names_group else {}
        await self._replace_name(s.item_names, s.item_key, item_id, item.name, **extra)
        await session.commit()
        return item_id

    async def group_of(self, kind: ReferenceKind, item_id: int) -> int | None:
        s = _spec(kind)
        return await self._session.scalar(select(s.item.c[s.group_key]).where(s.item.c[s.item_key] == item_id))

    async def delete_group(self, kind: ReferenceKind, group_id: int) -> None:
        s, session = _spec(kind), self._session
        item_ids = list(await session.scalars(select(s.item.c[s.item_key]).where(s.item.c[s.group_key] == group_id)))
        if item_ids:
            await session.execute(delete(s.item_names).where(s.item_names.c[s.item_key].in_(item_ids)))
            await session.execute(delete(s.item).where(s.item.c[s.item_key].in_(item_ids)))
        await session.execute(delete(s.group_names).where(s.group_names.c[s.group_key] == group_id))
        await session.execute(delete(s.group).where(s.group.c[s.group_key] == group_id))
        if kind == ReferenceKind.OPTIONS:  # no product has a value of it (checked), so only empty links remain
            await session.execute(delete(product_option).where(product_option.c.option_id == group_id))
        await session.commit()

    async def delete_item(self, kind: ReferenceKind, item_id: int) -> None:
        s = _spec(kind)
        await self._session.execute(delete(s.item_names).where(s.item_names.c[s.item_key] == item_id))
        await self._session.execute(delete(s.item).where(s.item.c[s.item_key] == item_id))
        await self._session.commit()

    async def _replace_name(self, table: Table, key: str, row_id: int, name: str, **extra: object) -> None:
        c = table.c
        await self._session.execute(delete(table).where(c[key] == row_id, c.language_id == self._language_id))
        await self._session.execute(insert(table).values({key: row_id, "language_id": self._language_id, "name": name,
                                                          **extra}))  # fmt: skip

    # Manufacturers

    async def manufacturers(self) -> list[ManufacturerDto]:
        m = manufacturer.c
        products = select(func.count()).where(DdProduct.manufacturer_id == m.manufacturer_id).scalar_subquery()
        rows = await self._session.execute(select(manufacturer, products).order_by(m.sort_order, m.name))
        return [
            ManufacturerDto(
                id=r.manufacturer_id, name=r.name, image=r.image or "", sort_order=r.sort_order, products=r[-1]
            )  # fmt: skip
            for r in rows
        ]

    async def save_manufacturer(self, item: ManufacturerDto) -> int:
        values = {"name": item.name, "image": item.image or None, "sort_order": item.sort_order}
        if item.id:
            query = update(manufacturer).where(manufacturer.c.manufacturer_id == item.id).values(values)
            await self._session.execute(query)
            manufacturer_id = item.id
        else:
            manufacturer_id = (await self._session.execute(insert(manufacturer).values(values))).inserted_primary_key[0]
            await self._session.execute(
                insert(manufacturer_to_store).values(manufacturer_id=manufacturer_id, store_id=STORE_ID)
            )
        await self._session.commit()
        return manufacturer_id

    async def delete_manufacturer(self, manufacturer_id: int) -> None:
        for table in (manufacturer_to_store, manufacturer):
            await self._session.execute(delete(table).where(table.c.manufacturer_id == manufacturer_id))
        await self._session.execute(delete(seo_url).where(seo_url.c.query == f"manufacturer_id={manufacturer_id}"))
        await self._session.commit()

    # Downloads

    async def downloads(self) -> list[DownloadDto]:
        d, n = download.c, download_description.c
        products = select(func.count()).where(product_to_download.c.download_id == d.download_id).scalar_subquery()
        rows = await self._session.execute(
            select(d.download_id, func.coalesce(n.name, ""), d.filename, d.mask, products)
            .outerjoin(download_description, (n.download_id == d.download_id) & (n.language_id == self._language_id))
            .order_by(n.name)
        )
        return [DownloadDto(id=r[0], name=r[1], filename=r[2], mask=r[3], products=r[4]) for r in rows]

    async def save_download(self, item: DownloadDto) -> int:
        values = {"filename": item.filename, "mask": item.mask}
        if item.id:
            await self._session.execute(update(download).where(download.c.download_id == item.id).values(values))
            download_id = item.id
        else:
            query = insert(download).values({**values, "date_added": datetime.now().replace(microsecond=0)})
            download_id = (await self._session.execute(query)).inserted_primary_key[0]
        await self._replace_name(download_description, "download_id", download_id, item.name)
        await self._session.commit()
        return download_id

    async def delete_download(self, download_id: int) -> None:
        for table in (download_description, download):
            await self._session.execute(delete(table).where(table.c.download_id == download_id))
        await self._session.commit()
