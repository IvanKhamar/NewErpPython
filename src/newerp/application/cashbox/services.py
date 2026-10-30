"""The Checkbox cash register: cashiers, shifts, fiscal receipts of orders, X and Z reports, automatic shift close."""

from collections.abc import Sequence
from datetime import date, datetime, timedelta

from newerp.application.cashbox.models import (
    AutoCloseResult,
    CashboxSetupDto,
    CashierDto,
    ChequeDto,
    ChequeFilter,
    DirectoryEntryDto,
    IssueChequeRequest,
    ReportTextDto,
    SaveCashierRequest,
    SaveDirectoryEntryRequest,
    WorkShiftDto,
    WorkShiftFilter,
    ZReportDto,
)
from newerp.application.cashbox.ports import CashboxRepository, CheckboxClient
from newerp.application.common import Clock, NotFoundError, PagedResult
from newerp.domain.cashbox import (
    CashboxDirectoryEntry,
    CashboxRequisites,
    CashboxWarehouse,
    Cashier,
    Cheque,
    FiscalReceipt,
    WorkShift,
    ZReport,
)
from newerp.domain.common import DomainError

AUTO_CLOSE_GRACE = timedelta(minutes=30)  # as the old site: a just-opened shift is left alone


class CashboxService:
    def __init__(self, repository: CashboxRepository, client: CheckboxClient, clock: Clock) -> None:
        self._repository = repository
        self._client = client
        self._clock = clock

    # ── Setup: cashiers, points of sale, requisites ──

    async def setup(self) -> CashboxSetupDto:
        cashiers = await self._repository.list_cashiers()
        warehouses = await self._repository.list_warehouses()
        requisites = await self._repository.list_requisites()
        open_shifts: dict[int, WorkShift] = {}
        for shift in await self._repository.list_open_shifts():
            latest = open_shifts.get(shift.cashier_id)
            if latest is None or shift.date_start > latest.date_start:
                open_shifts[shift.cashier_id] = shift

        def name_of(entries: Sequence[CashboxDirectoryEntry], entry_id: int) -> str | None:
            return next((e.name for e in entries if e.id == entry_id), None)

        rows = []
        for c in sorted(cashiers, key=lambda c: (c.payment_code == "", c.name)):
            shift = open_shifts.get(c.id)
            rows.append(
                CashierDto(
                    id=c.id,
                    name=c.name,
                    description=c.description,
                    payment_code=c.payment_code,
                    auto_close_time=c.auto_close_time,
                    warehouse_id=c.warehouse_id,
                    warehouse=name_of(warehouses, c.warehouse_id),
                    requisites_id=c.requisites_id,
                    requisites=name_of(requisites, c.requisites_id),
                    is_active=c.is_active,
                    can_fiscalize=self._client.can_use(c.id),
                    open_shift_id=shift.id if shift else None,
                    open_shift_start=shift.date_start if shift else None,
                )  # fmt: skip
            )
        return CashboxSetupDto(
            cashiers=rows, warehouses=_entries(warehouses), requisites=_entries(requisites),
            fake_mode=self._client.is_fake,
        )  # fmt: skip

    async def create_cashier(self, request: SaveCashierRequest) -> int:
        await self._check_references(request)
        cashier = Cashier(
            request.name, request.description, request.payment_code, request.auto_close_time, request.warehouse_id,
            request.requisites_id, request.is_active, self._clock.now(),
        )  # fmt: skip
        self._repository.add(cashier)
        await self._repository.save_changes()
        return cashier.id

    async def update_cashier(self, cashier_id: int, request: SaveCashierRequest) -> None:
        cashier = await self._require_cashier(cashier_id)
        await self._check_references(request)
        cashier.update(
            request.name, request.description, request.payment_code, request.auto_close_time, request.warehouse_id,
            request.requisites_id, request.is_active,
        )  # fmt: skip
        await self._repository.save_changes()

    async def create_warehouse(self, request: SaveDirectoryEntryRequest) -> int:
        warehouse = CashboxWarehouse(request.name, request.description, self._clock.now())
        self._repository.add(warehouse)
        await self._repository.save_changes()
        return warehouse.id

    async def update_warehouse(self, warehouse_id: int, request: SaveDirectoryEntryRequest) -> None:
        (await self._require_warehouse(warehouse_id)).rename(request.name, request.description)
        await self._repository.save_changes()

    async def delete_warehouse(self, warehouse_id: int) -> None:
        """The old site deleted without a check, leaving cashiers and receipts pointing nowhere."""
        warehouse = await self._require_warehouse(warehouse_id)
        if await self._repository.warehouse_in_use(warehouse_id):
            raise DomainError("Склад указано в касирів чи чеках, видалити його не можна.")
        await self._repository.remove(warehouse)
        await self._repository.save_changes()

    async def create_requisites(self, request: SaveDirectoryEntryRequest) -> int:
        requisites = CashboxRequisites(request.name, request.description, self._clock.now())
        self._repository.add(requisites)
        await self._repository.save_changes()
        return requisites.id

    async def update_requisites(self, requisites_id: int, request: SaveDirectoryEntryRequest) -> None:
        (await self._require_requisites(requisites_id)).rename(request.name, request.description)
        await self._repository.save_changes()

    async def delete_requisites(self, requisites_id: int) -> None:
        requisites = await self._require_requisites(requisites_id)
        if await self._repository.requisites_in_use(requisites_id):
            raise DomainError("Реквізити вказано в касирів чи чеках, видалити їх не можна.")
        await self._repository.remove(requisites)
        await self._repository.save_changes()

    # ── Shifts and reports ──

    async def shifts(self, shift_filter: WorkShiftFilter) -> list[WorkShiftDto]:
        return await self._repository.list_shifts(shift_filter)

    async def reports(self, cashier_id: int | None, from_: date | None, to: date | None) -> list[ZReportDto]:
        return await self._repository.list_reports(cashier_id, from_, to)

    async def open_shift(self, cashier_id: int, user_id: int) -> WorkShiftDto:
        """Opens a shift. As the old site: if Checkbox already has an open one (from elsewhere), it is taken."""
        cashier = await self._require_usable_cashier(cashier_id)
        if not cashier.is_active:
            raise DomainError(f"Касир «{cashier.name}» вимкнений.")
        if any(s.cashier_id == cashier_id for s in await self._repository.list_open_shifts()):
            raise DomainError(f"У касира «{cashier.name}» уже відкрита зміна.")
        checkbox_shift_id = await self._client.open_shift(cashier_id)
        shift = WorkShift(cashier_id, checkbox_shift_id, user_id, self._clock.now())
        self._repository.add(shift)
        await self._repository.save_changes()
        shifts = await self._repository.list_shifts(WorkShiftFilter(cashier_id=cashier_id))
        return next(s for s in shifts if s.id == shift.id)

    async def close_shift(self, cashier_id: int, user_id: int) -> ZReportDto | None:
        """Closes the shift in Checkbox (Z report) and here. None: Checkbox is still closing; the sync records it."""
        await self._require_usable_cashier(cashier_id)
        shift = _latest([s for s in await self._repository.list_open_shifts() if s.cashier_id == cashier_id])
        if shift is None:
            raise DomainError("У касира немає відкритої зміни.")
        z_report_id = await self._client.close_shift(cashier_id)
        return None if z_report_id is None else await self._finalize(shift, z_report_id, user_id)

    async def sync_shift(self, shift_id: int, user_id: int) -> ZReportDto | None:
        """If the register closed the shift itself (at midnight or elsewhere), the Z report is recorded here."""
        shift = await self._repository.get_shift(shift_id)
        if shift is None:
            raise NotFoundError(f"Зміну {shift_id} не знайдено.")
        if not shift.is_open:
            raise DomainError("Зміну вже закрито.")
        if not shift.checkbox_shift_id:
            raise DomainError("Зміна не має id у Checkbox, звірити її нема з чим.")
        await self._require_usable_cashier(shift.cashier_id)
        state = await self._client.get_shift(shift.cashier_id, shift.checkbox_shift_id)
        if state.is_closed and state.z_report_id:
            return await self._finalize(shift, state.z_report_id, user_id)
        return None

    async def auto_close(self) -> AutoCloseResult:
        """
        The scheduled check of open shifts (a cron job on the old site): first whether the register closed a shift
        itself, then closing those whose cashier's close time has come. One shift's error does not stop the others.
        """
        now = self._clock.now()
        shifts = [
            s
            for s in await self._repository.list_open_shifts()
            if s.checkbox_shift_id and s.date_start <= now - AUTO_CLOSE_GRACE and self._client.can_use(s.cashier_id)
        ]
        by_checkbox = by_schedule = 0
        errors: list[str] = []
        for shift in shifts:
            try:
                if await self.sync_shift(shift.id, 0) is not None:
                    by_checkbox += 1
                    continue
                cashier = await self._repository.get_cashier(shift.cashier_id)
                if cashier is None or cashier.auto_close_time is None:
                    continue
                close_at = datetime.combine(now.date(), cashier.auto_close_time)
                if now >= close_at and shift.date_start < close_at and await self.close_shift(shift.cashier_id, 0):
                    by_schedule += 1
            except DomainError as error:
                errors.append(f"Зміна {shift.id}: {error}")
        return AutoCloseResult(
            checked=len(shifts), closed_by_checkbox=by_checkbox, closed_by_schedule=by_schedule, errors=errors
        )

    async def x_report(self, cashier_id: int) -> ReportTextDto:
        """The X report: the shift's totals without closing it."""
        await self._require_usable_cashier(cashier_id)
        report_id = await self._client.create_x_report(cashier_id)
        return ReportTextDto(report_id=report_id, text=await self._client.get_report_text(cashier_id, report_id))

    async def z_report_text(self, report_id: int) -> ReportTextDto:
        report = next((r for r in await self._repository.list_reports(None, None, None) if r.id == report_id), None)
        if report is None:
            raise NotFoundError(f"Z-звіт {report_id} не знайдено.")
        await self._require_usable_cashier(report.cashier_id)
        text = await self._client.get_report_text(report.cashier_id, report.checkbox_report_id)
        return ReportTextDto(report_id=report.checkbox_report_id, text=text)

    # ── Receipts ──

    async def cheques(self, cheque_filter: ChequeFilter) -> PagedResult[ChequeDto]:
        page = await self._repository.list_cheques(cheque_filter)
        page.items = [self._with_url(c) for c in page.items]
        return page

    async def cheque_for_order(self, order_id: int) -> ChequeDto | None:
        cheque = await self._repository.get_cheque_for_order(order_id)
        return None if cheque is None else await self._dto(cheque.id)

    async def issue_for_order(self, order_id: int, request: IssueChequeRequest) -> ChequeDto:
        """
        Issues an order's fiscal receipt (the "Чек" button of the old site's order). If a receipt exists, no new one is
        made: an unfiscalized one is fiscalized again, a fiscalized one is returned as is.
        """
        order = await self._repository.get_order_for_cheque(order_id)
        if order is None:
            raise NotFoundError(f"Замовлення {order_id} не знайдено.")
        existing = await self._repository.get_cheque_for_order(order_id)
        if existing is not None:
            return await self._dto(existing.id) if existing.is_fiscalized else await self.fiscalize(existing.id)

        cashier = await self._pick_cashier(request.cashier_id, order.payment_code)
        shift = await self._require_open_shift(cashier)
        to_pay = order.total - order.prepayment
        # The receipt is built before anything is written: an order unfit for a receipt leaves no empty record.
        receipt = FiscalReceipt.build(
            order_id, cashier.name, order.lines, to_pay, order.payment_code, order.payment_method, order.email
        )
        cheque = Cheque(order_id, cashier, to_pay, shift.id, self._clock.now())
        self._repository.add(cheque)
        await self._repository.save_changes()
        # The record exists now, so if Checkbox refuses, the receipt stays unfiscalized in the list to retry.
        cheque.mark_fiscalized(await self._client.sell(cashier.id, receipt), shift.id)
        await self._repository.save_changes()
        return await self._dto(cheque.id)

    async def fiscalize(self, cheque_id: int) -> ChequeDto:
        """Fiscalizes again a receipt Checkbox did not accept ("Створити чек" in the old site's list)."""
        cheque = await self._repository.get_cheque(cheque_id)
        if cheque is None:
            raise NotFoundError(f"Чек {cheque_id} не знайдено.")
        if cheque.is_fiscalized:
            raise DomainError("Чек уже фіскалізовано.")
        order = await self._repository.get_order_for_cheque(cheque.order_id)
        if order is None:
            raise DomainError(f"Замовлення {cheque.order_id} чека не знайдено.")
        cashier = await self._require_usable_cashier(cheque.cashier_id)
        shift = await self._require_open_shift(cashier)
        receipt = FiscalReceipt.build(
            cheque.order_id, cashier.name, order.lines, cheque.sum, order.payment_code, order.payment_method,
            order.email,
        )  # fmt: skip
        cheque.mark_fiscalized(await self._client.sell(cashier.id, receipt), shift.id)
        await self._repository.save_changes()
        return await self._dto(cheque.id)

    async def delete_cheque(self, cheque_id: int) -> None:
        """Only a receipt that is not in Checkbox: a fiscalized one is already a sale registered with the tax office."""
        cheque = await self._repository.get_cheque(cheque_id)
        if cheque is None:
            raise NotFoundError(f"Чек {cheque_id} не знайдено.")
        if cheque.is_fiscalized:
            raise DomainError("Фіскалізований чек видалити не можна: продаж уже зареєстровано в податковій.")
        await self._repository.delete_cheque(cheque_id)

    async def _finalize(self, shift: WorkShift, z_report_id: str, user_id: int) -> ZReportDto:
        now = self._clock.now()
        report = ZReport(shift.cashier_id, z_report_id, user_id, now)
        self._repository.add(report)
        # All the cashier's open shifts are closed: Checkbox allows only one, so the rest are stale records.
        for open_shift in await self._repository.list_open_shifts():
            if open_shift.cashier_id == shift.cashier_id:
                open_shift.close(now)
        shift.close(now)
        await self._repository.save_changes()
        cheques = await self._repository.list_unreported_cheques(shift.cashier_id, shift.id, shift.date_start)
        await self._repository.attach_cheques_to_report([c.id for c in cheques], report.id)
        reports = await self._repository.list_reports(shift.cashier_id, None, None)
        return next(r for r in reports if r.id == report.id)

    async def _pick_cashier(self, cashier_id: int | None, payment_code: str) -> Cashier:
        """
        The chosen cashier, or the active one for the order's payment code, or the default one (no code).
        Because of a bug the old site always looked for the cashier with an empty payment code.
        """
        if cashier_id is not None:
            chosen = await self._require_usable_cashier(cashier_id)
            if not chosen.is_active:
                raise DomainError(f"Касир «{chosen.name}» вимкнений.")
            return chosen
        active = [c for c in await self._repository.list_cashiers() if c.is_active]
        cashier = next((c for c in active if payment_code and c.payment_code == payment_code), None) or next(
            (c for c in active if c.payment_code == ""), None
        )
        if cashier is None:
            raise DomainError(
                f"Немає активного касира для оплати «{payment_code}» і касира за замовчуванням. Оберіть касира вручну."
            )
        if not self._client.can_use(cashier.id):
            raise DomainError(f"Для касира «{cashier.name}» не задано ключі Checkbox.")
        return cashier

    async def _require_open_shift(self, cashier: Cashier) -> WorkShift:
        shift = _latest([s for s in await self._repository.list_open_shifts() if s.cashier_id == cashier.id])
        if shift is None:
            raise DomainError(f"У касира «{cashier.name}» немає відкритої зміни. Спершу відкрийте зміну.")
        return shift

    async def _require_cashier(self, cashier_id: int) -> Cashier:
        cashier = await self._repository.get_cashier(cashier_id)
        if cashier is None:
            raise NotFoundError(f"Касира {cashier_id} не знайдено.")
        return cashier

    async def _require_usable_cashier(self, cashier_id: int) -> Cashier:
        cashier = await self._require_cashier(cashier_id)
        if not self._client.can_use(cashier_id):
            raise DomainError(f"Для касира «{cashier.name}» не задано ключі Checkbox (налаштування checkbox.cashiers).")
        return cashier

    async def _require_warehouse(self, warehouse_id: int) -> CashboxWarehouse:
        warehouse = await self._repository.get_warehouse(warehouse_id)
        if warehouse is None:
            raise NotFoundError(f"Склад каси {warehouse_id} не знайдено.")
        return warehouse

    async def _require_requisites(self, requisites_id: int) -> CashboxRequisites:
        requisites = await self._repository.get_requisites(requisites_id)
        if requisites is None:
            raise NotFoundError(f"Реквізити {requisites_id} не знайдено.")
        return requisites

    async def _check_references(self, request: SaveCashierRequest) -> None:
        if request.warehouse_id != 0 and await self._repository.get_warehouse(request.warehouse_id) is None:
            raise DomainError(f"Склад каси {request.warehouse_id} не знайдено.")
        if request.requisites_id != 0 and await self._repository.get_requisites(request.requisites_id) is None:
            raise DomainError(f"Реквізити {request.requisites_id} не знайдено.")

    async def _dto(self, cheque_id: int) -> ChequeDto:
        return self._with_url(await self._repository.get_cheque_dto(cheque_id))

    def _with_url(self, dto: ChequeDto) -> ChequeDto:
        if not dto.is_fiscalized:
            return dto
        return dto.model_copy(update={"receipt_url": self._client.receipt_url(dto.checkbox_id)})


def _latest(shifts: list[WorkShift]) -> WorkShift | None:
    return max(shifts, key=lambda s: s.date_start, default=None)


def _entries(entries: Sequence[CashboxDirectoryEntry]) -> list[DirectoryEntryDto]:
    return [DirectoryEntryDto(id=e.id, name=e.name, description=e.description) for e in entries]
