"""Product returns in dd_return and their status log in dd_return_history."""

from datetime import datetime

from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.common import PagedResult
from newerp.application.returns import (
    NewReturnRecord,
    ReturnDetails,
    ReturnFilter,
    ReturnHistoryEntry,
    ReturnListItem,
    ReturnLookupItem,
    ReturnLookupsDto,
    ReturnSourceOrder,
)
from newerp.domain.returns import ReturnableLine
from newerp.infrastructure.models.customers import (
    DdReturn,
    DdReturnAction,
    DdReturnHistory,
    DdReturnReason,
    DdReturnStatus,
)
from newerp.infrastructure.models.orders import DdOrder, DdOrderProduct


class SqlReturnStore:
    def __init__(self, session: AsyncSession, language_id: int) -> None:
        self._session = session
        self._language_id = language_id

    async def list_page(self, return_filter: ReturnFilter) -> PagedResult[ReturnListItem]:
        r, f = DdReturn, return_filter
        conditions = []
        if f.return_id is not None:
            conditions.append(r.return_id == f.return_id)
        if f.order_id is not None:
            conditions.append(r.order_id == f.order_id)
        if f.status_id is not None:
            conditions.append(r.return_status_id == f.status_id)
        if f.search:
            conditions.append(
                func.concat(r.firstname, " ", r.lastname).contains(f.search, autoescape=True)
                | r.telephone.contains(f.search, autoescape=True)
                | r.product.contains(f.search, autoescape=True)
                | r.model.contains(f.search, autoescape=True)
            )
        total = await self._session.scalar(select(func.count()).select_from(r).where(*conditions))
        # date_ordered is not read: the real database may hold 0000-00-00 there.
        rows = await self._session.execute(
            select(
                r.return_id,
                r.order_id,
                r.firstname,
                r.lastname,
                r.telephone,
                r.product,
                r.model,
                r.quantity,
                r.return_status_id,
                r.return_action_id,
                r.date_added,
                r.date_modified,
            )  # fmt: skip
            .where(*conditions)
            .order_by(r.return_id.desc())
            .offset((f.page - 1) * f.page_size)
            .limit(f.page_size)
        )
        lookups = await self.lookups()
        statuses = {s.id: s.name for s in lookups.statuses}
        actions = {a.id: a.name for a in lookups.actions}
        items = [
            ReturnListItem(
                id=row.return_id,
                order_id=row.order_id,
                customer=f"{row.firstname} {row.lastname}".strip(),
                telephone=row.telephone,
                product=row.product,
                model=row.model,
                quantity=row.quantity,
                status_id=row.return_status_id,
                status_name=statuses.get(row.return_status_id),
                action_name=actions.get(row.return_action_id),
                date_added=row.date_added,
                date_modified=row.date_modified,
            )  # fmt: skip
            for row in rows
        ]
        return PagedResult[ReturnListItem](items=items, page=f.page, page_size=f.page_size, total_count=total or 0)

    async def get(self, return_id: int) -> ReturnDetails | None:
        r = await self._session.get(DdReturn, return_id)
        if r is None:
            return None
        lookups = await self.lookups()

        def name(items: list[ReturnLookupItem], item_id: int) -> str | None:
            return next((i.name for i in items if i.id == item_id), None)

        history = await self._session.execute(
            select(DdReturnHistory.date_added, DdReturnHistory.return_status_id, DdReturnHistory.comment)
            .where(DdReturnHistory.return_id == return_id)
            .order_by(DdReturnHistory.date_added, DdReturnHistory.return_history_id)
        )
        return ReturnDetails(
            id=r.return_id, order_id=r.order_id, product_id=r.product_id,
            customer=f"{r.firstname} {r.lastname}".strip(),
            telephone=r.telephone, email=r.email, product=r.product, model=r.model, quantity=r.quantity,
            opened=r.opened, reason_id=r.return_reason_id, reason_name=name(lookups.reasons, r.return_reason_id),
            action_id=r.return_action_id, action_name=name(lookups.actions, r.return_action_id),
            status_id=r.return_status_id, status_name=name(lookups.statuses, r.return_status_id),
            comment=r.comment or "", date_added=r.date_added, date_modified=r.date_modified,
            history=[
                ReturnHistoryEntry(
                    date_added=added, status_id=status_id, status_name=name(lookups.statuses, status_id),
                    comment=comment,
                )
                for added, status_id, comment in history
            ],
        )  # fmt: skip

    async def lookups(self) -> ReturnLookupsDto:
        async def items(model, id_column) -> list[ReturnLookupItem]:
            rows = await self._session.execute(
                select(id_column, model.name).where(model.language_id == self._language_id).order_by(id_column)
            )
            return [ReturnLookupItem(id=i, name=name) for i, name in rows]

        return ReturnLookupsDto(
            statuses=await items(DdReturnStatus, DdReturnStatus.return_status_id),
            reasons=await items(DdReturnReason, DdReturnReason.return_reason_id),
            actions=await items(DdReturnAction, DdReturnAction.return_action_id),
        )

    async def get_source_order(self, order_id: int) -> ReturnSourceOrder | None:
        o = DdOrder
        order = (
            await self._session.execute(
                select(o.firstname, o.lastname, o.telephone, o.email, o.customer_id, o.date_added).where(
                    o.order_id == order_id
                )
            )
        ).first()
        if order is None:
            return None
        p = DdOrderProduct
        lines = await self._session.execute(
            select(p.product_id, p.name, p.model, p.quantity).where(p.order_id == order_id).order_by(p.order_product_id)
        )
        returned = await self._session.execute(
            select(DdReturn.product_id, func.sum(DdReturn.quantity))
            .where(DdReturn.order_id == order_id)
            .group_by(DdReturn.product_id)
        )
        return ReturnSourceOrder(
            order_id, *order, [ReturnableLine(*line) for line in lines], {i: int(n) for i, n in returned}
        )

    async def create(self, record: NewReturnRecord) -> int:
        order, line, request = record.order, record.line, record.request
        comment = (request.comment or "").strip()
        row = DdReturn(
            order_id=order.order_id, product_id=line.product_id, customer_id=order.customer_id,
            firstname=order.firstname.strip(), lastname=order.lastname.strip(), email=order.email.strip(),
            telephone=order.telephone.strip(), product=line.name, model=line.model, quantity=request.quantity,
            opened=request.opened, return_reason_id=request.reason_id, return_action_id=request.action_id,
            return_status_id=request.status_id, comment=comment, date_ordered=order.date_added.date(),
            date_added=record.now, date_modified=record.now,
        )  # fmt: skip
        self._session.add(row)
        await self._session.flush()
        # Like the old form: the first status goes into the log too.
        self._session.add(
            DdReturnHistory(
                return_id=row.return_id, return_status_id=request.status_id, comment=comment, date_added=record.now
            )
        )
        await self._session.commit()
        return row.return_id

    async def add_history(
        self, return_id: int, status_id: int, comment: str, action_id: int | None, now: datetime
    ) -> bool:
        # MyISAM: no transaction, so first the status (like the old addReturnHistory), then the log.
        values: dict[str, object] = {"return_status_id": status_id, "date_modified": now}
        if action_id is not None:
            values["return_action_id"] = action_id
        result = await self._session.execute(update(DdReturn).where(DdReturn.return_id == return_id).values(values))
        if result.rowcount == 0:
            return False
        await self._session.execute(
            insert(DdReturnHistory).values(
                return_id=return_id, return_status_id=status_id, notify=False, comment=comment, date_added=now
            )
        )
        await self._session.commit()
        return True
