"""The Checkbox cash register: cashiers, shifts, receipts of orders, X and Z reports."""

from datetime import date
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from newerp.api.deps import ClockDep, Session
from newerp.api.security import CurrentPrincipal, Policies, require_roles
from newerp.application.cashbox.models import (
    AutoCloseResult,
    CashboxSetupDto,
    ChequeDto,
    ChequeFilter,
    IssueChequeRequest,
    ReportTextDto,
    SaveCashierRequest,
    SaveDirectoryEntryRequest,
    WorkShiftDto,
    WorkShiftFilter,
    ZReportDto,
)
from newerp.application.cashbox.services import CashboxService
from newerp.application.common import CreatedId, PagedResult
from newerp.config import get_settings
from newerp.infrastructure.cashbox.checkbox import checkbox_client
from newerp.infrastructure.cashbox.repository import SqlCashboxRepository

finance = [Depends(require_roles(*Policies.FINANCE))]
setup_only = [Depends(require_roles(*Policies.FINANCE_SETUP))]
router = APIRouter(prefix="/cashbox", tags=["Каса"], dependencies=finance)
order_router = APIRouter(prefix="/orders/{order_id}/cheque", tags=["Каса"], dependencies=finance)


@lru_cache
def get_checkbox():
    """One client for the process: it keeps cashiers' tokens (and the stub's open shifts)."""
    return checkbox_client(get_settings().checkbox)


def get_service(session: Session, clock: ClockDep) -> CashboxService:
    return CashboxService(SqlCashboxRepository(session), get_checkbox(), clock)


Service = Annotated[CashboxService, Depends(get_service)]
From = Annotated[date | None, Query(alias="from")]
CashierId = Annotated[int | None, Query(alias="cashierId")]


@router.get("/setup", summary="Касири, склади й реквізити; fakeMode — працює тестова заглушка Checkbox")
async def setup(service: Service) -> CashboxSetupDto:
    return await service.setup()


@router.post("/cashiers", dependencies=setup_only, summary="Новий касир (ключі Checkbox — у налаштуваннях)")
async def create_cashier(request: SaveCashierRequest, service: Service) -> CreatedId:
    return CreatedId(id=await service.create_cashier(request))


@router.put("/cashiers/{cashier_id}", status_code=204, dependencies=setup_only, summary="Змінити касира")
async def update_cashier(cashier_id: int, request: SaveCashierRequest, service: Service) -> None:
    await service.update_cashier(cashier_id, request)


@router.post("/warehouses", dependencies=setup_only, summary="Новий склад (точка продажу)")
async def create_warehouse(request: SaveDirectoryEntryRequest, service: Service) -> CreatedId:
    return CreatedId(id=await service.create_warehouse(request))


@router.put("/warehouses/{warehouse_id}", status_code=204, dependencies=setup_only, summary="Змінити склад")
async def update_warehouse(warehouse_id: int, request: SaveDirectoryEntryRequest, service: Service) -> None:
    await service.update_warehouse(warehouse_id, request)


@router.delete("/warehouses/{warehouse_id}", status_code=204, dependencies=setup_only, summary="Видалити склад")
async def delete_warehouse(warehouse_id: int, service: Service) -> None:
    await service.delete_warehouse(warehouse_id)


@router.post("/requisites", dependencies=setup_only, summary="Нові реквізити (ФОП)")
async def create_requisites(request: SaveDirectoryEntryRequest, service: Service) -> CreatedId:
    return CreatedId(id=await service.create_requisites(request))


@router.put("/requisites/{requisites_id}", status_code=204, dependencies=setup_only, summary="Змінити реквізити")
async def update_requisites(requisites_id: int, request: SaveDirectoryEntryRequest, service: Service) -> None:
    await service.update_requisites(requisites_id, request)


@router.delete("/requisites/{requisites_id}", status_code=204, dependencies=setup_only, summary="Видалити реквізити")
async def delete_requisites(requisites_id: int, service: Service) -> None:
    await service.delete_requisites(requisites_id)


@router.get("/shifts", summary="Зміни (останні 500) з кількістю й сумою фіскалізованих чеків")
async def shifts(
    service: Service, cashier_id: CashierId = None, from_: From = None, to: date | None = None
) -> list[WorkShiftDto]:
    return await service.shifts(WorkShiftFilter(cashier_id, from_, to))


@router.post("/cashiers/{cashier_id}/shift/open", summary="Відкрити зміну касира")
async def open_shift(cashier_id: int, principal: CurrentPrincipal, service: Service) -> WorkShiftDto:
    return await service.open_shift(cashier_id, principal.user_id)


@router.post(
    "/cashiers/{cashier_id}/shift/close",
    response_model=ZReportDto,
    summary="Закрити зміну (Z-звіт); 202 — Checkbox ще закриває зміну",
)
async def close_shift(cashier_id: int, principal: CurrentPrincipal, service: Service) -> ZReportDto | Response:
    report = await service.close_shift(cashier_id, principal.user_id)
    return report if report is not None else Response(status_code=202)


@router.post(
    "/shifts/{shift_id}/sync", response_model=ZReportDto, summary="Звірити зміну з Checkbox (204 — ще відкрита)"
)
async def sync_shift(shift_id: int, principal: CurrentPrincipal, service: Service) -> ZReportDto | Response:
    report = await service.sync_shift(shift_id, principal.user_id)
    return report if report is not None else Response(status_code=204)


@router.post("/shifts/auto-close", summary="Те саме, що фонова задача: звірити й закрити зміни за розкладом")
async def auto_close(service: Service) -> AutoCloseResult:
    return await service.auto_close()


@router.post("/cashiers/{cashier_id}/x-report", summary="X-звіт (підсумок зміни без закриття) текстом")
async def x_report(cashier_id: int, service: Service) -> ReportTextDto:
    return await service.x_report(cashier_id)


@router.get("/reports", summary="Z-звіти (останні 500)")
async def reports(
    service: Service, cashier_id: CashierId = None, from_: From = None, to: date | None = None
) -> list[ZReportDto]:
    return await service.reports(cashier_id, from_, to)


@router.get("/reports/{report_id}/text", summary="Текст Z-звіту з Checkbox")
async def report_text(report_id: int, service: Service) -> ReportTextDto:
    return await service.z_report_text(report_id)


@router.get("/cheques", summary="Чеки з фільтрами, посторінково")
async def cheques(
    service: Service,
    cashier_id: CashierId = None,
    order_id: Annotated[int | None, Query(alias="orderId")] = None,
    from_: From = None,
    to: date | None = None,
    fiscalized: bool | None = None,
    page: int = 1,
    page_size: Annotated[int, Query(alias="pageSize")] = 50,
) -> PagedResult[ChequeDto]:
    return await service.cheques(ChequeFilter(cashier_id, order_id, from_, to, fiscalized, page, page_size))


@router.post("/cheques/{cheque_id}/fiscalize", summary="Повторити фіскалізацію чека, якого Checkbox не прийняв")
async def fiscalize(cheque_id: int, service: Service) -> ChequeDto:
    return await service.fiscalize(cheque_id)


@router.delete("/cheques/{cheque_id}", status_code=204, summary="Видалити нефіскалізований чек")
async def delete_cheque(cheque_id: int, service: Service) -> None:
    await service.delete_cheque(cheque_id)


@order_router.get("", response_model=ChequeDto, summary="Чек замовлення (204 — чека ще немає)")
async def order_cheque(order_id: int, service: Service) -> ChequeDto | Response:
    cheque = await service.cheque_for_order(order_id)
    return cheque if cheque is not None else Response(status_code=204)


@order_router.post("", summary="Видати фіскальний чек (касир — вказаний або за кодом оплати)")
async def issue_cheque(order_id: int, request: IssueChequeRequest, service: Service) -> ChequeDto:
    return await service.issue_for_order(order_id, request)
