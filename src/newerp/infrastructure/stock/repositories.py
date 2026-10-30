from decimal import Decimal

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.common import LookupItem, ukrainian_key
from newerp.application.stock.models import (
    PurchaseOrderDto,
    PurchaseOrderLineDto,
    PurchaseOrderSummaryDto,
    ReorderLineDto,
    StockBalanceRow,
    StockItemDto,
    StockMovementDto,
    StockSupplierDto,
)
from newerp.application.stock.ports import StockItemFilter
from newerp.application.stock.services import message_text
from newerp.domain.stock import DRAFT, ORDERED, USE, PurchaseOrder, StockBalance, StockItem, StockTransfer
from newerp.infrastructure.stock.mapping import (
    purchase_order_lines,
    purchase_orders,
    stock_categories,
    stock_items,
    stock_locations,
    stock_log,
    stock_suppliers,
    stock_transfers,
)


class SqlStockRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_categories(self) -> list[LookupItem]:
        rows = await self._session.execute(
            select(stock_categories.c.category_id, stock_categories.c.name).order_by(
                stock_categories.c.sort_order, stock_categories.c.name
            )
        )
        return [LookupItem(id=category_id, name=name) for category_id, name in rows]

    async def list_suppliers(self) -> list[StockSupplierDto]:
        s = stock_suppliers.c
        rows = await self._session.execute(
            select(s.supplier_id, s.name, s.phone, s.email, s.categories, s.notes).order_by(s.name)
        )
        return [
            StockSupplierDto(id=i, name=n, phone=p, email=e, categories=c, notes=notes) for i, n, p, e, c, notes in rows
        ]

    async def list_items(self, item_filter: StockItemFilter) -> list[StockItemDto]:
        i = stock_items.c
        conditions = [i.status.is_(True)]
        if item_filter.category_id is not None:
            conditions.append(i.category_id == item_filter.category_id)
        if item_filter.below_minimum_only:
            conditions.append(i.stock < i.min_stock)
        return await self._project(*conditions)

    async def get_item_dto(self, item_id: int) -> StockItemDto | None:
        items = await self._project(stock_items.c.item_id == item_id)
        return items[0] if items else None

    async def list_movements(self, item_id: int) -> list[StockMovementDto]:
        m = stock_log.c
        rows = await self._session.execute(
            select(
                m.log_id,
                m.action,
                m.quantity,
                m.stock_before,
                m.stock_after,
                m.comment,
                m.user_id,
                m.username,
                m.date_added,
            )  # fmt: skip
            .where(m.item_id == item_id)
            .order_by(m.date_added.desc(), m.log_id.desc())
        )
        return [
            StockMovementDto(
                id=log_id,
                item_id=item_id,
                delta=-quantity if action == USE else quantity,
                quantity_before=before,
                quantity_after=after,
                reason=comment,
                user_id=user_id,
                username=username,
                created_at=created_at,
            )  # fmt: skip
            for log_id, action, quantity, before, after, comment, user_id, username, created_at in rows
        ]

    async def category_exists(self, category_id: int) -> bool:
        return await self._exists(stock_categories.c.category_id == category_id)

    async def category_name_taken(self, name: str) -> bool:
        return await self._exists(stock_categories.c.name == name)

    async def supplier_exists(self, supplier_id: int) -> bool:
        return await self._exists(stock_suppliers.c.supplier_id == supplier_id)

    async def find_item(self, item_id: int) -> StockItem | None:
        return await self._session.get(StockItem, item_id)

    def add(self, entity: object) -> None:
        self._session.add(entity)

    async def save_changes(self) -> None:
        await self._session.commit()

    async def _exists(self, condition) -> bool:
        return bool(await self._session.scalar(select(exists().where(condition))))

    async def _project(self, *conditions) -> list[StockItemDto]:
        # Category and supplier are optional (NULL in the old module), so both joins are LEFT JOIN.
        i, c, s = stock_items.c, stock_categories.c, stock_suppliers.c
        rows = await self._session.execute(
            select(
                i.item_id,
                i.name,
                i.unit,
                i.category_id,
                c.name,
                i.supplier_id,
                s.name,
                i.stock,
                i.min_stock,
                i.used_for,
                i.price,
            )  # fmt: skip
            .select_from(stock_items)
            .outerjoin(stock_categories, c.category_id == i.category_id)
            .outerjoin(stock_suppliers, s.supplier_id == i.supplier_id)
            .where(*conditions)
        )
        items = [
            StockItemDto(
                id=item_id, name=name, unit=unit, category_id=category_id, category_name=category_name,
                supplier_id=supplier_id, supplier_name=supplier_name, quantity=quantity, min_quantity=minimum,
                is_below_minimum=quantity < minimum, used_for=used_for, price=price,
            )  # fmt: skip
            for item_id, name, unit, category_id, category_name, supplier_id, supplier_name, quantity, minimum,
            used_for, price in rows
        ]  # fmt: skip
        return sorted(items, key=lambda item: ukrainian_key(item.name))


class SqlPurchaseOrderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_orders(self, status: str | None) -> list[PurchaseOrderSummaryDto]:
        o, s, line = purchase_orders.c, stock_suppliers.c, purchase_order_lines.c
        counts = (
            select(line.order_id, func.count().label("lines"), func.sum(line.quantity * line.price).label("total"))
            .group_by(line.order_id)
            .subquery()
        )
        query = (
            select(o.id, o.supplier_id, s.name, o.status, o.created_at, counts.c.lines, counts.c.total)
            .select_from(purchase_orders)
            .outerjoin(stock_suppliers, s.supplier_id == o.supplier_id)
            .outerjoin(counts, counts.c.order_id == o.id)
            .order_by(o.created_at.desc(), o.id.desc())
        )
        if status:
            query = query.where(o.status == status)
        rows = await self._session.execute(query)
        return [
            PurchaseOrderSummaryDto(
                id=order_id,
                supplier_id=supplier_id,
                supplier_name=supplier_name,
                status=order_status,
                created_at=created_at,
                line_count=lines or 0,
                total=total or Decimal(0),
            )  # fmt: skip
            for order_id, supplier_id, supplier_name, order_status, created_at, lines, total in rows
        ]

    async def get_dto(self, order_id: int) -> PurchaseOrderDto | None:
        order = await self.find(order_id)
        if order is None:
            return None
        s = stock_suppliers.c
        supplier = (
            await self._session.execute(select(s.name, s.phone).where(s.supplier_id == order.supplier_id))
        ).first()
        item_ids = [line.item_id for line in order.lines]
        i = stock_items.c
        items = {
            item_id: (name, unit)
            for item_id, name, unit in await self._session.execute(
                select(i.item_id, i.name, i.unit).where(i.item_id.in_(item_ids))
            )
        }
        lines = [
            PurchaseOrderLineDto(
                id=line.id,
                item_id=line.item_id,
                item_name=items[line.item_id][0] if line.item_id in items else None,
                unit=items[line.item_id][1] if line.item_id in items else None,
                quantity=line.quantity,
                price=line.price,
                sum=line.quantity * line.price,
                received=line.received_quantity,
                remaining=line.remaining,
            )
            for line in order.lines
        ]
        lines.sort(key=lambda line: ukrainian_key(line.item_name or ""))
        message = message_text(
            ReorderLineDto(
                item_id=line.item_id,
                name=line.item_name or f"позиція {line.item_id}",
                unit=line.unit or "",
                quantity=0,
                min_quantity=0,
                on_order=0,
                suggested=line.quantity,
                price=line.price,
                sum=line.sum,
            )  # fmt: skip
            for line in lines
        )
        return PurchaseOrderDto(
            id=order.id,
            supplier_id=order.supplier_id,
            supplier_name=supplier[0] if supplier else None,
            supplier_phone=supplier[1] if supplier else None,
            status=order.status,
            comment=order.comment,
            created_at=order.created_at,
            ordered_at=order.ordered_at,
            closed_at=order.closed_at,
            lines=lines,
            total=sum((line.sum for line in lines), Decimal(0)),
            message_text=message,
        )

    async def find(self, order_id: int) -> PurchaseOrder | None:
        return await self._session.get(PurchaseOrder, order_id)

    async def on_order(self) -> dict[int, Decimal]:
        o, line = purchase_orders.c, purchase_order_lines.c
        rows = await self._session.execute(
            select(line.item_id, line.quantity, line.received_quantity)
            .join(purchase_orders, o.id == line.order_id)
            .where(o.status.in_((DRAFT, ORDERED)))
        )
        result: dict[int, Decimal] = {}
        for item_id, quantity, received in rows:
            result[item_id] = result.get(item_id, Decimal(0)) + max(Decimal(0), quantity - received)
        return result

    async def find_items(self, item_ids: list[int]) -> list[StockItem]:
        if not item_ids:
            return []
        return list(
            await self._session.scalars(
                select(StockItem).where(stock_items.c.status.is_(True), stock_items.c.item_id.in_(item_ids))
            )
        )

    def add(self, entity: object) -> None:
        self._session.add(entity)

    async def save_changes(self) -> None:
        await self._session.commit()


class SqlStockLocationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_balances(self, item_id: int) -> list[StockBalance]:
        return list(await self._session.scalars(select(StockBalance).where(stock_locations.c.item_id == item_id)))

    async def list_balances(self) -> list[StockBalanceRow]:
        b = stock_locations.c
        rows = await self._session.execute(select(b.item_id, b.warehouse_id, b.quantity).where(b.quantity != 0))
        return [StockBalanceRow(item_id=i, warehouse_id=w, quantity=q) for i, w, q in rows]

    async def list_transfers(self, item_id: int) -> list[StockTransfer]:
        t = stock_transfers.c
        return list(
            await self._session.scalars(
                select(StockTransfer).where(t.item_id == item_id).order_by(t.created_at.desc(), t.id.desc())
            )
        )

    async def holds_stock(self, warehouse_id: int) -> bool:
        b = stock_locations.c
        return bool(await self._session.scalar(select(exists().where(b.warehouse_id == warehouse_id, b.quantity != 0))))

    def add(self, entity: object) -> None:
        self._session.add(entity)
