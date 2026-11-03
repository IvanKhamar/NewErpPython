"""Size filters over dd_product_option_value, dd_filter and dd_product_filter."""

from sqlalchemy import and_, delete, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.infrastructure.catalog.product_edit import SIZE_OPTION_NAME
from newerp.infrastructure.models.catalog import (
    DdFilter,
    DdFilterDescription,
    DdOptionDescription,
    DdOptionValueDescription,
    DdProductFilter,
    DdProductOptionValue,
)

SIZE_FILTER_GROUP_ID = 3  # "Розмір" among the storefront filters (the old console's constant)


class SqlMaintenanceStore:
    def __init__(self, session: AsyncSession, language_id: int) -> None:
        self._session = session
        self._language_id = language_id

    async def product_sizes(self) -> list[tuple[int, str]]:
        v, n, o = DdProductOptionValue, DdOptionValueDescription, DdOptionDescription
        rows = await self._session.execute(
            select(v.product_id, n.name)
            .join(n, (n.option_value_id == v.option_value_id) & (n.language_id == self._language_id))
            .join(o, (o.option_id == v.option_id) & (o.language_id == self._language_id))
            .where(o.name.contains(SIZE_OPTION_NAME))
        )
        return [(product_id, name) for product_id, name in rows]

    async def size_filters(self) -> dict[str, int]:
        d = DdFilterDescription
        rows = await self._session.execute(
            select(d.name, d.filter_id).where(
                d.filter_group_id == SIZE_FILTER_GROUP_ID, d.language_id == self._language_id
            )
        )
        return {name.strip(): filter_id for name, filter_id in rows}

    async def create_size_filter(self, name: str) -> int:
        result = await self._session.execute(
            insert(DdFilter).values(filter_group_id=SIZE_FILTER_GROUP_ID, sort_order=0)
        )
        filter_id = result.inserted_primary_key[0]
        await self._session.execute(
            insert(DdFilterDescription).values(
                filter_id=filter_id, language_id=self._language_id, filter_group_id=SIZE_FILTER_GROUP_ID, name=name
            )
        )
        await self._session.commit()
        return filter_id

    async def size_links(self) -> set[tuple[int, int]]:
        rows = await self._session.execute(
            select(DdProductFilter.product_id, DdProductFilter.filter_id)
            .join(DdFilter, DdFilter.filter_id == DdProductFilter.filter_id)
            .where(DdFilter.filter_group_id == SIZE_FILTER_GROUP_ID)
        )
        return {(product_id, filter_id) for product_id, filter_id in rows}

    async def change_links(self, add: set[tuple[int, int]], remove: set[tuple[int, int]]) -> None:
        if remove:
            pairs = [and_(DdProductFilter.product_id == p, DdProductFilter.filter_id == f) for p, f in remove]
            await self._session.execute(delete(DdProductFilter).where(or_(*pairs)))
        if add:
            await self._session.execute(insert(DdProductFilter), [{"product_id": p, "filter_id": f} for p, f in add])
        await self._session.commit()
