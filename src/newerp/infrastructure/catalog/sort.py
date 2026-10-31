"""SQL side of the product order: groups, sales, pins, snapshots and the cross-sell panel."""

from collections import defaultdict
from collections.abc import Callable
from datetime import datetime
from itertools import batched

from sqlalchemy import case, delete, exists, func, insert, literal, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.catalog.sort import ProductSortOptions, SortPinDto, SortProductDto, SortSnapshotDto
from newerp.domain.catalog import CROSS_SELL_KEY, SortGroup, SortGroupType, SortPin, code_to_type, type_to_code
from newerp.infrastructure.models.catalog import (
    DdCategoryDescription,
    DdCrosssellItem,
    DdCrosssellSort,
    DdFilter,
    DdFilterDescription,
    DdProduct,
    DdProductDescription,
    DdProductFilter,
    DdProductLabel,
    DdProductManualPin,
    DdProductOrderSnapshot,
    DdProductOrderSnapshotItem,
    DdProductToCategory,
    DdProductToLabel,
    DdProductToStore,
)
from newerp.infrastructure.models.orders import DdOrder, DdOrderProduct
from newerp.infrastructure.models.settings import DdSetting

CROSS_SELL_METRIC_KEY = "crosssell_sort_metric"
NO_CROSS_SELL_POSITION = 999999
CHUNK = 500


class SqlProductSortStore:
    def __init__(
        self, session: AsyncSession, language_id: int, options: ProductSortOptions, now: Callable[[], datetime]
    ) -> None:
        self._session = session
        self._language_id = language_id
        self._options = options
        self._now = now

    async def list_groups(self) -> list[SortGroup]:
        groups: list[SortGroup] = []
        tags = await self._session.execute(
            select(DdFilter.filter_id, DdFilterDescription.name)
            .join(DdFilterDescription, DdFilterDescription.filter_id == DdFilter.filter_id)
            .where(
                DdFilter.filter_group_id == self._options.tag_filter_group_id,
                DdFilterDescription.language_id == self._language_id,
            )
            .order_by(DdFilter.filter_id)
        )
        groups += [
            SortGroup(SortGroupType.TAG, name, "За темою: " + _tag_title(name), filter_id=filter_id)
            for filter_id, name in tags
        ]

        # Only categories with at least one enabled product. A category with products in colors splits per color.
        category_ids = list(
            await self._session.scalars(
                select(DdProductToCategory.category_id)
                .join(DdProduct, DdProduct.product_id == DdProductToCategory.product_id)
                .where(DdProduct.status.is_(True))
                .distinct()
                .order_by(DdProductToCategory.category_id)
            )
        )
        color_rows = await self._session.execute(
            select(DdProductToCategory.category_id, DdProductFilter.filter_id, DdFilterDescription.name)
            .join(DdProduct, DdProduct.product_id == DdProductToCategory.product_id)
            .join(DdProductFilter, DdProductFilter.product_id == DdProductToCategory.product_id)
            .join(DdFilterDescription, DdFilterDescription.filter_id == DdProductFilter.filter_id)
            .where(
                DdProduct.status.is_(True),
                DdProductFilter.filter_id.in_(self._options.color_filter_ids),
                DdFilterDescription.language_id == self._language_id,
            )
            .distinct()
        )
        colors: dict[int, list[tuple[int, str]]] = defaultdict(list)
        for category_id, filter_id, name in sorted(color_rows, key=lambda c: c[1]):
            colors[category_id].append((filter_id, name))
        names = await self._category_names(category_ids)
        for category_id in category_ids:
            name = names.get(category_id) or f"Категорія #{category_id}"
            if colors[category_id]:
                groups += [
                    SortGroup(
                        SortGroupType.CATEGORY_COLOR,
                        f"{category_id}_{filter_id}",
                        f"{name} — {color}",
                        category_id,
                        filter_id,
                    )  # fmt: skip
                    for filter_id, color in colors[category_id]
                ]
            else:
                groups.append(SortGroup(SortGroupType.CATEGORY, str(category_id), name, category_id))

        labels = await self._session.execute(
            select(DdProductLabel.label_id, DdProductLabel.name)
            .where(DdProductLabel.status.is_(True))
            .order_by(DdProductLabel.label_id)
        )
        groups += [SortGroup(SortGroupType.LABEL, name, "Мітка: " + name, label_id=i) for i, name in labels]
        groups.append(SortGroup(SortGroupType.CROSS_SELL, CROSS_SELL_KEY, "Допродаж (оформлення)"))
        return groups

    async def group_product_ids(self, group: SortGroup) -> list[int]:
        p = DdProduct
        enabled = select(p.product_id).where(p.status.is_(True))
        match group.type:
            case SortGroupType.CATEGORY | SortGroupType.CATEGORY_COLOR:
                query = enabled.where(
                    exists().where(
                        DdProductToCategory.product_id == p.product_id,
                        DdProductToCategory.category_id == group.category_id,
                    )
                )
                if group.filter_id is not None:
                    query = query.where(_has_filter(group.filter_id))
                query = query.order_by(p.product_id)
            case SortGroupType.TAG:
                query = enabled.where(_has_filter(group.filter_id)).order_by(p.product_id)
            case SortGroupType.LABEL:
                query = enabled.where(
                    exists().where(
                        DdProductToLabel.product_id == p.product_id, DdProductToLabel.label_id == group.label_id
                    )
                ).order_by(p.date_added.desc(), p.product_id)
            case SortGroupType.CROSS_SELL:
                query = self._cross_sell_products()
        return list(await self._session.scalars(query))

    async def sales(
        self, product_ids: list[int], start: datetime, end: datetime, cross_sell_only: bool
    ) -> dict[int, int]:
        o, op = DdOrder, DdOrderProduct
        query = (
            select(op.product_id, func.sum(op.quantity))
            .join(o, o.order_id == op.order_id)
            .where(
                op.product_id.in_(product_ids),
                o.date_added >= start,
                o.date_added <= end,
                o.order_status_id.in_(self._options.counted_status_ids),
                o.user_id.not_in(self._options.excluded_user_ids),
            )
            .group_by(op.product_id)
        )
        if cross_sell_only:
            query = query.where(op.crosssell.is_(True))
        # Wholesale and partner orders do not show what people buy in the shop (MySQL LIKE ignores case).
        for marker in self._options.excluded_order_markers:
            query = query.where(
                *(~column.contains(marker, autoescape=True) for column in (o.firstname, o.lastname, o.payment_company))
            )
        for marker in [*self._options.excluded_order_markers, *self._options.excluded_comment_markers]:
            query = query.where(~o.comment.contains(marker, autoescape=True))
        return {product_id: int(total) for product_id, total in await self._session.execute(query)}

    async def products(self, product_ids: list[int], cross_sell: bool) -> list[SortProductDto]:
        order = (
            func.coalesce(DdCrosssellSort.sort_order, NO_CROSS_SELL_POSITION) if cross_sell else DdProduct.sort_order
        )
        rows = await self._session.execute(
            select(DdProduct.product_id, func.coalesce(DdProductDescription.name, ""), DdProduct.image, order)
            .outerjoin(DdProductDescription, self._description_of(DdProduct.product_id))
            .outerjoin(DdCrosssellSort, DdCrosssellSort.product_id == DdProduct.product_id)
            .where(DdProduct.product_id.in_(product_ids))
        )
        return [
            SortProductDto(product_id=i, name=name, image=image or "", sort_order=sort_order)
            for i, name, image, sort_order in rows
        ]

    async def apply(self, cross_sell: bool, ordered_product_ids: list[int]) -> int:
        if cross_sell:
            # The panel's products may have changed, so its order is rewritten whole, as on the old site.
            await self._session.execute(delete(DdCrosssellSort))
            await self._session.execute(
                insert(DdCrosssellSort),
                [{"product_id": i, "sort_order": n} for n, i in enumerate(ordered_product_ids, start=1)],
            )
        else:
            # One UPDATE … CASE for a few hundred products instead of a query per product.
            positions = {product_id: n for n, product_id in enumerate(ordered_product_ids, start=1)}
            for chunk in batched(ordered_product_ids, CHUNK, strict=False):  # the last chunk may be shorter
                await self._session.execute(
                    update(DdProduct)
                    .where(DdProduct.product_id.in_(chunk))
                    .values(sort_order=case({i: positions[i] for i in chunk}, value=DdProduct.product_id))
                    .execution_options(synchronize_session=False)
                )
        await self._session.commit()
        return len(ordered_product_ids)

    async def pins(self, group: SortGroup) -> list[SortPin]:
        rows = await self._session.execute(
            select(DdProductManualPin.position, DdProductManualPin.product_id)
            .where(DdProductManualPin.group_type == group.type_code, DdProductManualPin.group_key == group.key)
            .order_by(DdProductManualPin.position, DdProductManualPin.id)
        )
        return [SortPin(position, product_id) for position, product_id in rows]

    async def list_pins(self) -> list[SortPinDto]:
        pin = DdProductManualPin
        rows = await self._session.execute(
            select(
                pin.id,
                pin.group_type,
                pin.group_key,
                pin.position,
                pin.product_id,
                func.coalesce(DdProductDescription.name, ""),
            )  # fmt: skip
            .outerjoin(DdProductDescription, self._description_of(pin.product_id))
            .order_by(pin.group_type, pin.group_key, pin.position)
        )
        known = {type_to_code(t) for t in SortGroupType}
        return [
            SortPinDto(
                id=i, type=code_to_type(code), key=key, position=position, product_id=product_id, product_name=name
            )
            for i, code, key, position, product_id, name in rows
            if code in known
        ]

    async def set_pin(self, group: SortGroup, position: int, product_id: int, now: datetime) -> None:
        pin = DdProductManualPin
        await self._session.execute(
            delete(pin).where(
                pin.group_type == group.type_code,
                pin.group_key == group.key,
                (pin.product_id == product_id) | (pin.position == position),
            )
        )
        self._session.add(
            pin(
                group_type=group.type_code,
                group_key=group.key,
                position=position,
                product_id=product_id,
                created_at=now,
            )
        )
        await self._session.commit()

    async def remove_pin(self, pin_id: int) -> bool:
        result = await self._session.execute(delete(DdProductManualPin).where(DdProductManualPin.id == pin_id))
        await self._session.commit()
        return result.rowcount > 0

    async def create_snapshot(self, note: str, now: datetime) -> int:
        snapshot = DdProductOrderSnapshot(note=note[:255], created_at=now)
        self._session.add(snapshot)
        await self._session.flush()
        await self._session.execute(
            insert(DdProductOrderSnapshotItem).from_select(
                ["snapshot_id", "product_id", "sort_order"],
                select(literal(snapshot.snapshot_id), DdProduct.product_id, DdProduct.sort_order),
            )
        )
        await self._session.commit()
        return snapshot.snapshot_id

    async def list_snapshots(self, limit: int) -> list[SortSnapshotDto]:
        s = DdProductOrderSnapshot
        rows = await self._session.execute(
            select(s.snapshot_id, s.note, s.created_at).order_by(s.created_at.desc(), s.snapshot_id.desc()).limit(limit)
        )
        return [SortSnapshotDto(id=i, note=note, created_at=created_at) for i, note, created_at in rows]

    async def restore_snapshot(self, snapshot_id: int) -> bool:
        if await self._session.get(DdProductOrderSnapshot, snapshot_id) is None:
            return False
        item = DdProductOrderSnapshotItem
        await self._session.execute(
            update(DdProduct)
            .where(DdProduct.product_id == item.product_id, item.snapshot_id == snapshot_id)
            .values(sort_order=item.sort_order)
            .execution_options(synchronize_session=False)
        )
        await self._session.commit()
        return True

    async def cross_sell_panel_only(self) -> bool:
        query = select(DdSetting.setting_id).where(
            DdSetting.store_id == 0, DdSetting.key == CROSS_SELL_METRIC_KEY, DdSetting.value == "panel"
        )
        return bool(await self._session.scalar(select(exists(query))))

    async def set_cross_sell_panel_only(self, panel_only: bool) -> None:
        await self._session.execute(
            delete(DdSetting).where(DdSetting.store_id == 0, DdSetting.key == CROSS_SELL_METRIC_KEY)
        )
        self._session.add(
            DdSetting(
                store_id=0,
                code="crosssell_sort",
                key=CROSS_SELL_METRIC_KEY,
                value="panel" if panel_only else "all",
                serialized=False,
            )  # fmt: skip
        )
        await self._session.commit()

    async def search_products(self, term: str, limit: int) -> list[SortProductDto]:
        rows = await self._session.execute(
            select(DdProduct.product_id, DdProductDescription.name, DdProduct.image, DdProduct.sort_order)
            .join(DdProductDescription, self._description_of(DdProduct.product_id))
            .where(DdProduct.status.is_(True), DdProductDescription.name.contains(term, autoescape=True))
            .order_by(DdProductDescription.name)
            .limit(limit)
        )
        return [
            SortProductDto(product_id=i, name=name, image=image or "", sort_order=sort_order)
            for i, name, image, sort_order in rows
        ]

    def _cross_sell_products(self):
        """The panel's products as on the storefront: single products and products of chosen categories, enabled,
        in store 0 and already available, by the product's current order."""
        p = DdProduct
        single = exists().where(DdCrosssellItem.type == "product", DdCrosssellItem.target_id == p.product_id)
        by_category = exists().where(
            DdProductToCategory.product_id == p.product_id,
            DdCrosssellItem.target_id == DdProductToCategory.category_id,
            DdCrosssellItem.type == "category",
        )
        return (
            select(p.product_id)
            .where(
                p.status.is_(True),
                p.date_available <= self._now(),
                exists().where(DdProductToStore.product_id == p.product_id, DdProductToStore.store_id == 0),
                single | by_category,
            )
            .order_by(p.sort_order, p.product_id)
        )

    async def _category_names(self, category_ids: list[int]) -> dict[int, str]:
        rows = await self._session.execute(
            select(DdCategoryDescription.category_id, DdCategoryDescription.name).where(
                DdCategoryDescription.language_id == self._language_id,
                DdCategoryDescription.category_id.in_(category_ids),
            )
        )
        return {category_id: name for category_id, name in rows}

    def _description_of(self, product_id_column):
        return (DdProductDescription.product_id == product_id_column) & (
            DdProductDescription.language_id == self._language_id
        )


def _has_filter(filter_id: int | None):
    return exists().where(DdProductFilter.product_id == DdProduct.product_id, DdProductFilter.filter_id == filter_id)


def _tag_title(name: str) -> str:
    """Like ucfirst(str_replace(':', ' ', name)) in the old code."""
    title = name.replace(":", " ")
    return title[:1].upper() + title[1:]
