from datetime import date, datetime
from typing import Protocol

from newerp.application.cashbox.models import (
    CheckboxShiftState,
    ChequeDto,
    ChequeFilter,
    OrderForCheque,
    WorkShiftDto,
    WorkShiftFilter,
    ZReportDto,
)
from newerp.application.common import PagedResult
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


class CheckboxClient(Protocol):
    """
    The Checkbox RRO API. Every action is on behalf of a cashier (only the implementation knows their login,
    password and license key). Without keys a stub is used that either can do nothing or (fake_receipts) gives test
    receipts, so local development never fiscalizes a real sale. Checkbox refusals become DomainError.
    """

    @property
    def is_fake(self) -> bool: ...

    def can_use(self, cashier_id: int) -> bool:
        """Whether the cashier's keys are set (or test receipts are on)."""
        ...

    async def open_shift(self, cashier_id: int) -> str:
        """The cashier's open shift in Checkbox, or a new one. Returns the shift id."""
        ...

    async def get_shift(self, cashier_id: int, shift_id: str) -> CheckboxShiftState: ...

    async def close_shift(self, cashier_id: int) -> str | None:
        """Closes the shift (Z report). None if Checkbox is still closing it (the shift sync records the report)."""
        ...

    async def create_x_report(self, cashier_id: int) -> str: ...

    async def get_report_text(self, cashier_id: int, report_id: str) -> str: ...

    async def sell(self, cashier_id: int, receipt: FiscalReceipt) -> str:
        """Fiscalizes a sale receipt. Returns the receipt id in Checkbox."""
        ...

    def receipt_url(self, receipt_id: str) -> str | None:
        """The receipt's public page (no keys in it); None: the stub has no page."""
        ...


class CashboxRepository(Protocol):
    async def list_cashiers(self) -> list[Cashier]: ...

    async def get_cashier(self, cashier_id: int) -> Cashier | None: ...

    async def list_warehouses(self) -> list[CashboxWarehouse]: ...

    async def get_warehouse(self, warehouse_id: int) -> CashboxWarehouse | None: ...

    async def list_requisites(self) -> list[CashboxRequisites]: ...

    async def get_requisites(self, requisites_id: int) -> CashboxRequisites | None: ...

    async def warehouse_in_use(self, warehouse_id: int) -> bool:
        """Whether cashiers or receipts refer to the point of sale; then it cannot be deleted."""
        ...

    async def requisites_in_use(self, requisites_id: int) -> bool: ...

    async def remove(self, entry: CashboxDirectoryEntry) -> None: ...

    async def list_open_shifts(self) -> list[WorkShift]: ...

    async def get_shift(self, shift_id: int) -> WorkShift | None: ...

    async def list_shifts(self, shift_filter: WorkShiftFilter) -> list[WorkShiftDto]: ...

    async def list_reports(self, cashier_id: int | None, from_: date | None, to: date | None) -> list[ZReportDto]: ...

    async def list_unreported_cheques(self, cashier_id: int, shift_id: int, since: datetime) -> list[Cheque]:
        """The cashier's receipts not yet closed by a Z report, issued in this shift or (without one) after it began."""
        ...

    async def attach_cheques_to_report(self, cheque_ids: list[int], report_id: int) -> None: ...

    async def get_cheque(self, cheque_id: int) -> Cheque | None: ...

    async def get_cheque_for_order(self, order_id: int) -> Cheque | None: ...

    async def get_cheque_dto(self, cheque_id: int) -> ChequeDto | None: ...

    async def list_cheques(self, cheque_filter: ChequeFilter) -> PagedResult[ChequeDto]: ...

    async def delete_cheque(self, cheque_id: int) -> None: ...

    async def get_order_for_cheque(self, order_id: int) -> OrderForCheque | None: ...

    def add(self, entity: Cashier | CashboxDirectoryEntry | WorkShift | ZReport | Cheque) -> None: ...

    async def save_changes(self) -> None: ...
