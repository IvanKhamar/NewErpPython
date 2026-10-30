from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import and_, delete, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.cashbox.models import (
    ChequeDto,
    ChequeFilter,
    OrderForCheque,
    WorkShiftDto,
    WorkShiftFilter,
    ZReportDto,
)
from newerp.application.common import PagedResult, normalize_paging
from newerp.domain.cashbox import (
    CashboxDirectoryEntry,
    CashboxRequisites,
    CashboxWarehouse,
    Cashier,
    Cheque,
    ReceiptSourceLine,
    WorkShift,
)
from newerp.infrastructure.cashbox.mapping import (
    cashbox_requisites,
    cashbox_warehouses,
    cashiers,
    cheques,
    work_shifts,
    z_reports,
)
from newerp.infrastructure.models.orders import DdOrder, DdOrderProduct
from newerp.infrastructure.models.users import DdUser

LIST_LIMIT = 500


def _day_range(column, from_: date | None, to: date | None) -> list:
    conditions = []
    if from_ is not None:
        conditions.append(column >= datetime.combine(from_, time.min))
    if to is not None:
        conditions.append(column < datetime.combine(to + timedelta(days=1), time.min))
    return conditions


def _user_name():
    return func.concat(DdUser.firstname, " ", DdUser.lastname)


class SqlCashboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_cashiers(self) -> list[Cashier]:
        return list(await self._session.scalars(select(Cashier)))

    async def get_cashier(self, cashier_id: int) -> Cashier | None:
        return await self._session.get(Cashier, cashier_id)

    async def list_warehouses(self) -> list[CashboxWarehouse]:
        return list(await self._session.scalars(select(CashboxWarehouse).order_by(cashbox_warehouses.c.warehouse_id)))

    async def get_warehouse(self, warehouse_id: int) -> CashboxWarehouse | None:
        return await self._session.get(CashboxWarehouse, warehouse_id)

    async def list_requisites(self) -> list[CashboxRequisites]:
        return list(await self._session.scalars(select(CashboxRequisites).order_by(cashbox_requisites.c.requisites_id)))

    async def get_requisites(self, requisites_id: int) -> CashboxRequisites | None:
        return await self._session.get(CashboxRequisites, requisites_id)

    async def warehouse_in_use(self, warehouse_id: int) -> bool:
        return await self._any(cashiers.c.warehouse_id == warehouse_id) or await self._any(
            cheques.c.cashbox_warehouse_id == warehouse_id
        )

    async def requisites_in_use(self, requisites_id: int) -> bool:
        return await self._any(cashiers.c.requisites_id == requisites_id) or await self._any(
            cheques.c.cashbox_requisites_id == requisites_id
        )

    async def remove(self, entry: CashboxDirectoryEntry) -> None:
        await self._session.delete(entry)

    async def list_open_shifts(self) -> list[WorkShift]:
        return list(await self._session.scalars(select(WorkShift).where(work_shifts.c.date_end.is_(None))))

    async def get_shift(self, shift_id: int) -> WorkShift | None:
        return await self._session.get(WorkShift, shift_id)

    async def list_shifts(self, shift_filter: WorkShiftFilter) -> list[WorkShiftDto]:
        s, ch = work_shifts.c, cheques.c
        fiscal = and_(ch.cashbox_work_shift_id == s.work_shift_id, ch.checkbox_id != "")
        count = select(func.count()).where(fiscal).scalar_subquery()
        total = select(func.coalesce(func.sum(ch.sum), 0)).where(fiscal).scalar_subquery()
        query = (
            select(
                s.work_shift_id,
                s.cashbox_user_id,
                func.coalesce(cashiers.c.name, ""),
                s.checkbox_shift_id,
                _user_name(),
                s.date_start,
                s.date_end,
                count,
                total,
            )  # fmt: skip
            .select_from(work_shifts)
            .outerjoin(cashiers, cashiers.c.user_id == s.cashbox_user_id)
            .outerjoin(DdUser, DdUser.user_id == s.user_id)
            .where(*_day_range(s.date_start, shift_filter.from_, shift_filter.to))
            .order_by(s.work_shift_id.desc())
            .limit(LIST_LIMIT)
        )
        if shift_filter.cashier_id is not None:
            query = query.where(s.cashbox_user_id == shift_filter.cashier_id)
        return [
            WorkShiftDto(
                id=r[0],
                cashier_id=r[1],
                cashier=r[2],
                checkbox_shift_id=r[3],
                opened_by=r[4].strip() if r[4] else None,
                date_start=r[5],
                date_end=r[6],
                cheques=r[7],
                cheques_sum=r[8],
            )  # fmt: skip
            for r in await self._session.execute(query)
        ]

    async def list_reports(self, cashier_id: int | None, from_: date | None, to: date | None) -> list[ZReportDto]:
        r = z_reports.c
        query = (
            select(
                r.report_id,
                r.cashbox_user_id,
                func.coalesce(cashiers.c.name, ""),
                r.z_report_id,
                _user_name(),
                r.date_added,
            )  # fmt: skip
            .select_from(z_reports)
            .outerjoin(cashiers, cashiers.c.user_id == r.cashbox_user_id)
            .outerjoin(DdUser, DdUser.user_id == r.user_id)
            .where(*_day_range(r.date_added, from_, to))
            .order_by(r.report_id.desc())
            .limit(LIST_LIMIT)
        )
        if cashier_id is not None:
            query = query.where(r.cashbox_user_id == cashier_id)
        return [
            ZReportDto(
                id=row[0],
                cashier_id=row[1],
                cashier=row[2],
                checkbox_report_id=row[3],
                closed_by=row[4].strip() if row[4] else None,
                date_added=row[5],
            )  # fmt: skip
            for row in await self._session.execute(query)
        ]

    async def list_unreported_cheques(self, cashier_id: int, shift_id: int, since: datetime) -> list[Cheque]:
        c = cheques.c
        return list(
            await self._session.scalars(
                select(Cheque).where(
                    c.cashbox_user_id == cashier_id,
                    c.cashbox_report_id == 0,
                    or_(c.cashbox_work_shift_id == shift_id, and_(c.cashbox_work_shift_id == 0, c.date_added >= since)),
                )
            )
        )

    async def attach_cheques_to_report(self, cheque_ids: list[int], report_id: int) -> None:
        if not cheque_ids:
            return
        await self._session.execute(
            update(cheques).where(cheques.c.cheque_id.in_(cheque_ids)).values(cashbox_report_id=report_id)
        )
        await self._session.commit()

    async def get_cheque(self, cheque_id: int) -> Cheque | None:
        return await self._session.get(Cheque, cheque_id)

    async def get_cheque_for_order(self, order_id: int) -> Cheque | None:
        return await self._session.scalar(
            select(Cheque).where(cheques.c.order_id == order_id).order_by(cheques.c.cheque_id.desc()).limit(1)
        )

    async def get_cheque_dto(self, cheque_id: int) -> ChequeDto | None:
        rows = await self._project([cheques.c.cheque_id == cheque_id])
        return rows[0] if rows else None

    async def list_cheques(self, cheque_filter: ChequeFilter) -> PagedResult[ChequeDto]:
        f, c = cheque_filter, cheques.c
        page, page_size = normalize_paging(f.page, f.page_size)
        conditions = _day_range(c.date_added, f.from_, f.to)
        if f.cashier_id is not None:
            conditions.append(c.cashbox_user_id == f.cashier_id)
        if f.order_id is not None:
            conditions.append(c.order_id == f.order_id)
        if f.fiscalized is not None:
            conditions.append(c.checkbox_id != "" if f.fiscalized else c.checkbox_id == "")
        total = await self._session.scalar(select(func.count()).select_from(cheques).where(*conditions))
        items = await self._project(conditions, offset=(page - 1) * page_size, limit=page_size)
        return PagedResult[ChequeDto](items=items, page=page, page_size=page_size, total_count=total or 0)

    async def delete_cheque(self, cheque_id: int) -> None:
        await self._session.execute(delete(cheques).where(cheques.c.cheque_id == cheque_id))
        await self._session.commit()

    async def get_order_for_cheque(self, order_id: int) -> OrderForCheque | None:
        o = await self._session.get(DdOrder, order_id)
        if o is None:
            return None
        lines = await self._session.execute(
            select(DdOrderProduct.product_id, DdOrderProduct.name, DdOrderProduct.price, DdOrderProduct.quantity)
            .where(DdOrderProduct.order_id == order_id)
            .order_by(DdOrderProduct.order_product_id)
        )
        return OrderForCheque(
            order_id=o.order_id, total=o.total, prepayment=o.prepayment, payment_code=o.payment_code,
            payment_method=o.payment_method, email=o.email, lines=[ReceiptSourceLine(*line) for line in lines],
        )  # fmt: skip

    def add(self, entity: object) -> None:
        self._session.add(entity)

    async def save_changes(self) -> None:
        await self._session.commit()

    async def _any(self, condition) -> bool:
        return bool(await self._session.scalar(select(exists().where(condition))))

    async def _project(self, conditions: list, offset: int = 0, limit: int | None = None) -> list[ChequeDto]:
        c = cheques.c
        query = (
            select(
                c.cheque_id,
                c.order_id,
                func.concat(DdOrder.firstname, " ", DdOrder.lastname),
                c.cashbox_user_id,
                cashiers.c.name,
                cashbox_warehouses.c.name,
                cashbox_requisites.c.name,
                c.sum,
                c.checkbox_id,
                c.cashbox_work_shift_id,
                c.cashbox_report_id,
                c.date_added,
            )  # fmt: skip
            .select_from(cheques)
            .outerjoin(DdOrder, DdOrder.order_id == c.order_id)
            .outerjoin(cashiers, cashiers.c.user_id == c.cashbox_user_id)
            .outerjoin(cashbox_warehouses, cashbox_warehouses.c.warehouse_id == c.cashbox_warehouse_id)
            .outerjoin(cashbox_requisites, cashbox_requisites.c.requisites_id == c.cashbox_requisites_id)
            .where(*conditions)
            .order_by(c.cheque_id.desc())
            .offset(offset)
        )
        if limit is not None:
            query = query.limit(limit)
        return [
            ChequeDto(
                id=r[0],
                order_id=r[1],
                customer=r[2].strip() if r[2] else None,
                cashier_id=r[3],
                cashier=r[4],
                warehouse=r[5],
                requisites=r[6],
                sum=r[7] if r[7] is not None else Decimal(0),
                checkbox_id=r[8],
                is_fiscalized=r[8] != "",
                receipt_url=None,
                work_shift_id=r[9],
                report_id=r[10],
                date_added=r[11],
            )  # fmt: skip
            for r in await self._session.execute(query)
        ]
