from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

from newerp.application.common import Dto, Money
from newerp.domain.cashbox import ReceiptSourceLine


class CashierDto(Dto):
    """can_fiscalize: Checkbox keys are set for the cashier (or the test stub works). open_shift_id: if any."""

    id: int
    name: str
    description: str
    payment_code: str
    auto_close_time: time | None
    warehouse_id: int
    warehouse: str | None
    requisites_id: int
    requisites: str | None
    is_active: bool
    can_fiscalize: bool
    open_shift_id: int | None
    open_shift_start: datetime | None


class SaveCashierRequest(Dto):
    name: str | None = None
    description: str | None = None
    payment_code: str | None = None
    auto_close_time: time | None = None
    warehouse_id: int = 0
    requisites_id: int = 0
    is_active: bool = False


class DirectoryEntryDto(Dto):
    id: int
    name: str
    description: str


class SaveDirectoryEntryRequest(Dto):
    name: str | None = None
    description: str | None = None


class CashboxSetupDto(Dto):
    """Everything to set up the register in one request. fake_mode: no keys, the Checkbox test stub works."""

    cashiers: list[CashierDto]
    warehouses: list[DirectoryEntryDto]
    requisites: list[DirectoryEntryDto]
    fake_mode: bool


class WorkShiftDto(Dto):
    id: int
    cashier_id: int
    cashier: str
    checkbox_shift_id: str
    opened_by: str | None
    date_start: datetime
    date_end: datetime | None
    cheques: int
    cheques_sum: Money


@dataclass(frozen=True)
class WorkShiftFilter:
    cashier_id: int | None = None
    from_: date | None = None
    to: date | None = None


class ZReportDto(Dto):
    id: int
    cashier_id: int
    cashier: str
    checkbox_report_id: str
    closed_by: str | None
    date_added: datetime


class ReportTextDto(Dto):
    """A Checkbox report as text, as the register prints it."""

    report_id: str
    text: str


class AutoCloseResult(Dto):
    """How many shifts were checked, closed by the register itself, and closed by us on schedule."""

    checked: int
    closed_by_checkbox: int
    closed_by_schedule: int
    errors: list[str]


class ChequeDto(Dto):
    """receipt_url: the receipt's page on the Checkbox service (to print or send to the customer)."""

    id: int
    order_id: int
    customer: str | None
    cashier_id: int
    cashier: str | None
    warehouse: str | None
    requisites: str | None
    sum: Money
    checkbox_id: str
    is_fiscalized: bool
    receipt_url: str | None
    work_shift_id: int
    report_id: int
    date_added: datetime


@dataclass(frozen=True)
class ChequeFilter:
    cashier_id: int | None = None
    order_id: int | None = None
    from_: date | None = None
    to: date | None = None
    fiscalized: bool | None = None
    page: int = 1
    page_size: int = 50


class IssueChequeRequest(Dto):
    """cashier_id: which cashier to use; None: by the order's payment code, as on the old site."""

    cashier_id: int | None = None


@dataclass(frozen=True)
class OrderForCheque:
    order_id: int
    total: Decimal
    prepayment: Decimal
    payment_code: str
    payment_method: str
    email: str
    lines: list[ReceiptSourceLine]


@dataclass(frozen=True)
class CheckboxShiftState:
    """A shift on the Checkbox side. z_report_id: when it is already closed (for example, by the register itself)."""

    shift_id: str
    is_closed: bool
    z_report_id: str | None
