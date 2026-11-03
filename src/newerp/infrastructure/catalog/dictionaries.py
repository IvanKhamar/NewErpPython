"""Labels, search presets and categories over the OpenCart tables, as the old admin wrote them.

Categories keep OpenCart's closure table dd_category_path (every category with each of its parents and their level),
which the storefront reads for breadcrumbs and subcategory product lists; it is rebuilt from parent_id after each
save, like the old "repair"."""

from datetime import datetime

from sqlalchemy import Boolean, Column, Integer, MetaData, String, Table, delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.catalog.dictionaries import (
    CategoryEditDto,
    CategoryFormDto,
    CategoryListItem,
    LabelDto,
    SearchPresetDto,
)
from newerp.application.common import LookupItem, ukrainian_key
from newerp.infrastructure.catalog.product_edit import STORE_ID, seo_url
from newerp.infrastructure.models.catalog import (
    DdCategory,
    DdCategoryDescription,
    DdFilter,
    DdFilterDescription,
    DdProductLabel,
    DdProductToCategory,
    DdProductToLabel,
)
from newerp.infrastructure.models.marketing import DdCouponCategory

metadata = MetaData()
search_preset = Table(
    "dd_search_preset",
    metadata,
    Column("preset_id", Integer, primary_key=True),
    Column("name", String(255)),
    Column("url", String(255)),
    Column("sort_order", Integer),
    Column("status", Boolean),
)
category_path = Table(
    "dd_category_path",
    metadata,
    Column("category_id", Integer, primary_key=True),
    Column("path_id", Integer, primary_key=True),
    Column("level", Integer),
)
category_to_store = Table(
    "dd_category_to_store",
    metadata,
    Column("category_id", Integer, primary_key=True),
    Column("store_id", Integer, primary_key=True),
)
category_filter = Table(
    "dd_category_filter",
    metadata,
    Column("category_id", Integer, primary_key=True),
    Column("filter_id", Integer, primary_key=True),
)

DESCRIPTION_FIELDS = (
    "name", "product_name", "advertising_product_name", "description", "description_table_size", "meta_title",
    "meta_description", "meta_keyword",
)  # fmt: skip


class SqlDictionaryStore:
    def __init__(self, session: AsyncSession, language_id: int) -> None:
        self._session = session
        self._language_id = language_id

    # Labels

    async def labels(self) -> list[LabelDto]:
        products = select(func.count()).where(DdProductToLabel.label_id == DdProductLabel.label_id).scalar_subquery()
        rows = await self._session.execute(
            select(DdProductLabel, products).order_by(DdProductLabel.sort_order, DdProductLabel.name)
        )
        return [
            LabelDto(
                id=label.label_id,
                name=label.name,
                background_color=label.background_color,
                text_color=label.text_color,
                sort_order=label.sort_order,
                enabled=label.status,
                products=count,
            )  # fmt: skip
            for label, count in rows
        ]

    async def save_label(self, label: LabelDto) -> int:
        values = {"name": label.name, "background_color": label.background_color, "text_color": label.text_color,
                  "sort_order": label.sort_order, "status": label.enabled}  # fmt: skip
        if label.id:
            await self._session.execute(
                update(DdProductLabel).where(DdProductLabel.label_id == label.id).values(values)
            )
            label_id = label.id
        else:
            label_id = (await self._session.execute(insert(DdProductLabel).values(values))).inserted_primary_key[0]
        await self._session.commit()
        return label_id

    async def delete_label(self, label_id: int) -> bool:
        result = await self._session.execute(delete(DdProductLabel).where(DdProductLabel.label_id == label_id))
        await self._session.execute(delete(DdProductToLabel).where(DdProductToLabel.label_id == label_id))
        await self._session.commit()
        return result.rowcount > 0

    # Search presets

    async def presets(self) -> list[SearchPresetDto]:
        p = search_preset.c
        rows = await self._session.execute(select(search_preset).order_by(p.sort_order, p.name))
        return [
            SearchPresetDto(id=r.preset_id, name=r.name, url=r.url, sort_order=r.sort_order, enabled=r.status)
            for r in rows
        ]

    async def save_preset(self, preset: SearchPresetDto) -> int:
        values = {"name": preset.name, "url": preset.url, "sort_order": preset.sort_order, "status": preset.enabled}
        if preset.id:
            await self._session.execute(
                update(search_preset).where(search_preset.c.preset_id == preset.id).values(values)
            )
            preset_id = preset.id
        else:
            preset_id = (await self._session.execute(insert(search_preset).values(values))).inserted_primary_key[0]
        await self._session.commit()
        return preset_id

    async def delete_preset(self, preset_id: int) -> bool:
        result = await self._session.execute(delete(search_preset).where(search_preset.c.preset_id == preset_id))
        await self._session.commit()
        return result.rowcount > 0

    # Categories

    async def categories(self) -> list[CategoryListItem]:
        c, d = DdCategory, DdCategoryDescription
        products = select(func.count()).where(DdProductToCategory.category_id == c.category_id).scalar_subquery()
        rows = (
            await self._session.execute(
                select(
                    c.category_id, func.coalesce(d.name, ""), c.parent_id, c.sort_order, c.status, products
                ).outerjoin(d, (d.category_id == c.category_id) & (d.language_id == self._language_id))
            )
        ).all()
        names = {r[0]: r[1] or f"#{r[0]}" for r in rows}
        parents = {r[0]: r[2] for r in rows}

        def full_name(category_id: int) -> str:
            parts, seen, current = [], set(), category_id
            while current in names and current not in seen:
                seen.add(current)
                parts.append(names[current])
                current = parents[current]
            return " > ".join(reversed(parts))

        items = [
            CategoryListItem(
                id=r[0], name=full_name(r[0]), parent_id=r[2], sort_order=r[3], enabled=r[4], products=r[5]
            )
            for r in rows
        ]
        return sorted(items, key=lambda item: ukrainian_key(item.name))

    async def category(self, category_id: int) -> CategoryEditDto | None:
        c, d = DdCategory, DdCategoryDescription
        row = (
            await self._session.execute(
                select(
                    c.parent_id,
                    c.image,
                    c.image_table_size,
                    c.top,
                    c.column,
                    c.sort_order,
                    c.status,
                    c.production_period,
                    c.banner_id,
                ).where(c.category_id == category_id)  # fmt: skip
            )
        ).first()
        if row is None:
            return None
        text = (
            await self._session.execute(
                select(*(getattr(d, name) for name in DESCRIPTION_FIELDS)).where(
                    d.category_id == category_id, d.language_id == self._language_id
                )
            )
        ).first()
        keyword = await self._session.scalar(
            select(seo_url.c.keyword).where(
                seo_url.c.query == f"category_id={category_id}", seo_url.c.store_id == STORE_ID
            )  # fmt: skip
        )
        filters = await self._session.scalars(
            select(category_filter.c.filter_id).where(category_filter.c.category_id == category_id)
        )
        return CategoryEditDto(
            id=category_id, parent_id=row[0], image=row[1] or "", image_table_size=row[2] or "", top=row[3],
            column=row[4], sort_order=row[5], enabled=row[6], production_period=row[7], banner_id=row[8] or 0,
            seo_keyword=keyword or "", filter_ids=list(filters),
            **{name: (value or "") for name, value in zip(DESCRIPTION_FIELDS, text or ("",) * 8, strict=True)},
        )  # fmt: skip

    async def filters(self) -> list[LookupItem]:
        rows = await self._session.execute(
            select(DdFilter.filter_id, DdFilterDescription.name, DdFilter.filter_group_id)
            .join(
                DdFilterDescription,
                (DdFilterDescription.filter_id == DdFilter.filter_id)
                & (DdFilterDescription.language_id == self._language_id),
            )
            .order_by(DdFilter.filter_group_id, DdFilter.sort_order)
        )
        return [LookupItem(id=i, name=f"{g}: {n}") for i, n, g in rows]

    async def seo_keyword_owner(self, keyword: str) -> str | None:
        return await self._session.scalar(
            select(seo_url.c.query).where(seo_url.c.keyword == keyword, seo_url.c.store_id == STORE_ID).limit(1)
        )

    async def save_category(self, category_id: int | None, form: CategoryFormDto, now: datetime) -> int:
        s, c = self._session, DdCategory
        values = {
            "parent_id": form.parent_id, "image": form.image or None, "image_table_size": form.image_table_size or None,
            "top": form.top, "column": form.column, "sort_order": form.sort_order, "status": form.enabled,
            "production_period": form.production_period, "banner_id": form.banner_id, "date_modified": now,
        }  # fmt: skip
        if category_id is None:
            category_id = (await s.execute(insert(c).values({**values, "date_added": now}))).inserted_primary_key[0]
            await s.execute(insert(category_to_store).values(category_id=category_id, store_id=STORE_ID))
        else:
            await s.execute(update(c).where(c.category_id == category_id).values(values))
        d = DdCategoryDescription
        await s.execute(delete(d).where(d.category_id == category_id, d.language_id == self._language_id))
        await s.execute(
            insert(d).values(
                category_id=category_id,
                language_id=self._language_id,
                **{name: getattr(form, name) for name in DESCRIPTION_FIELDS},
            )  # fmt: skip
        )
        await s.execute(delete(category_filter).where(category_filter.c.category_id == category_id))
        if form.filter_ids:
            await s.execute(
                insert(category_filter), [{"category_id": category_id, "filter_id": i} for i in form.filter_ids]
            )
        await s.execute(delete(seo_url).where(seo_url.c.query == f"category_id={category_id}",
                                              seo_url.c.store_id == STORE_ID))  # fmt: skip
        if form.seo_keyword:
            await s.execute(
                insert(seo_url).values(
                    store_id=STORE_ID,
                    language_id=self._language_id,
                    query=f"category_id={category_id}",
                    keyword=form.seo_keyword,
                )  # fmt: skip
            )
        await s.commit()
        await self.repair_paths()
        return category_id

    async def has_children(self, category_id: int) -> bool:
        return bool(await self._session.scalar(select(func.count()).where(DdCategory.parent_id == category_id)))

    async def delete_category(self, category_id: int) -> None:
        s = self._session
        for table, column in (
            (category_path, category_path.c.category_id),
            (category_filter, category_filter.c.category_id),
            (category_to_store, category_to_store.c.category_id),
            (DdProductToCategory, DdProductToCategory.category_id),
            (DdCouponCategory, DdCouponCategory.category_id),
            (DdCategoryDescription, DdCategoryDescription.category_id),
            (DdCategory, DdCategory.category_id),
        ):
            await s.execute(delete(table).where(column == category_id))
        await s.execute(delete(seo_url).where(seo_url.c.query == f"category_id={category_id}"))
        await s.commit()

    async def repair_paths(self) -> int:
        """Every category gets its parents from the root down (level 0) and itself last. A broken parent chain
        (a missing parent or a loop) stops where it breaks, so the repair always ends."""
        parents = dict((await self._session.execute(select(DdCategory.category_id, DdCategory.parent_id))).all())
        rows = []
        for category_id in parents:
            chain, seen, current = [], set(), category_id
            while current and current in parents and current not in seen:
                seen.add(current)
                chain.append(current)
                current = parents[current]
            rows += [{"category_id": category_id, "path_id": p, "level": n} for n, p in enumerate(reversed(chain))]
        await self._session.execute(delete(category_path))
        if rows:
            await self._session.execute(insert(category_path), rows)
        await self._session.commit()
        return len(parents)
