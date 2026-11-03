"""The five status lists over dd_order_status, dd_return_status, dd_return_reason, dd_return_action,
dd_stock_status: rows of (id, language_id, name), the id shared by the languages."""

from dataclasses import dataclass

from sqlalchemy import Column, Integer, MetaData, String, Table, delete, func, insert, literal, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.status_lists import StatusItemDto, StatusList
from newerp.infrastructure.models.catalog import DdProduct
from newerp.infrastructure.models.customers import DdReturn
from newerp.infrastructure.models.orders import DdOrder, DdOrderStatusLog

metadata = MetaData()


def _list(name: str, key: str, *extra: Column) -> Table:
    return Table(
        name, metadata, Column(key, Integer, primary_key=True), Column("language_id", Integer, primary_key=True),
        Column("name", String(32)), *extra,
    )  # fmt: skip


@dataclass(frozen=True)
class Spec:
    table: Table
    key: str
    used_by: tuple  # columns whose rows "use" an item


SPECS: dict[str, Spec] = {
    "order-statuses": Spec(
        _list("dd_order_status", "order_status_id", Column("color", String(7))),
        "order_status_id",
        (DdOrder.order_status_id, DdOrderStatusLog.new_status_id),
    ),
    "return-statuses": Spec(
        _list("dd_return_status", "return_status_id"), "return_status_id", (DdReturn.return_status_id,)
    ),
    "return-reasons": Spec(
        _list("dd_return_reason", "return_reason_id"), "return_reason_id", (DdReturn.return_reason_id,)
    ),
    "return-actions": Spec(
        _list("dd_return_action", "return_action_id"), "return_action_id", (DdReturn.return_action_id,)
    ),
    "stock-statuses": Spec(
        _list("dd_stock_status", "stock_status_id"), "stock_status_id", (DdProduct.stock_status_id,)
    ),
}


class SqlStatusListStore:
    def __init__(self, session: AsyncSession, language_id: int) -> None:
        self._session = session
        self._language_id = language_id

    async def items(self, kind: StatusList) -> list[StatusItemDto]:
        spec = SPECS[kind]
        t = spec.table.c
        used = [select(func.count()).where(column == t[spec.key]).scalar_subquery() for column in spec.used_by]
        color = t.color if "color" in t else literal("")
        rows = await self._session.execute(
            select(t[spec.key], t.name, color, sum(used[1:], used[0]))
            .where(t.language_id == self._language_id)
            .order_by(t[spec.key])
        )
        return [StatusItemDto(id=r[0], name=r[1], color=r[2] or "", used=r[3]) for r in rows]

    async def save(self, kind: StatusList, item: StatusItemDto) -> int:
        spec = SPECS[kind]
        t = spec.table.c
        values: dict[str, object] = {"name": item.name}
        if "color" in t:
            values["color"] = item.color
        if item.id:
            await self._session.execute(
                update(spec.table).where(t[spec.key] == item.id, t.language_id == self._language_id).values(values)
            )
            item_id = item.id
        else:
            # The id is shared by the languages, so the new one is the largest plus one, not an AUTO_INCREMENT.
            item_id = (await self._session.scalar(select(func.coalesce(func.max(t[spec.key]), 0)))) + 1
            await self._session.execute(
                insert(spec.table).values({spec.key: item_id, "language_id": self._language_id, **values})
            )
        await self._session.commit()
        return item_id

    async def delete(self, kind: StatusList, item_id: int) -> None:
        spec = SPECS[kind]
        await self._session.execute(delete(spec.table).where(spec.table.c[spec.key] == item_id))
        await self._session.commit()
