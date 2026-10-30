"""Maps the cashbox domain classes to the reconstructed dd_cashbox_* tables of the old site."""

from sqlalchemy import Boolean, Column, DateTime, Integer, Numeric, String, Table, Text

from newerp.domain.cashbox import CashboxRequisites, CashboxWarehouse, Cashier, Cheque, WorkShift, ZReport
from newerp.infrastructure.db import Base
from newerp.infrastructure.types import TimeOfDay

metadata = Base.metadata

cashiers = Table(
    "dd_cashbox_user",
    metadata,
    Column("user_id", Integer, primary_key=True),
    Column("name", String(255)),
    Column("description", Text),
    Column("payment_code", String(128)),
    Column("auto_close_time", TimeOfDay, nullable=True),
    Column("warehouse_id", Integer),
    Column("requisites_id", Integer),
    Column("status", Boolean),
    Column("date_added", DateTime),
)


def _directory(name: str, id_column: str) -> Table:
    return Table(
        name,
        metadata,
        Column(id_column, Integer, primary_key=True),
        Column("name", String(255)),
        Column("description", Text),
        Column("date_added", DateTime),
    )


cashbox_warehouses = _directory("dd_cashbox_warehouse", "warehouse_id")
cashbox_requisites = _directory("dd_cashbox_requisites", "requisites_id")

work_shifts = Table(
    "dd_cashbox_work_shift",
    metadata,
    Column("work_shift_id", Integer, primary_key=True),
    Column("user_id", Integer),
    Column("cashbox_user_id", Integer),
    Column("checkbox_shift_id", String(64)),
    Column("date_added", DateTime),
    Column("date_start", DateTime),
    Column("date_end", DateTime, nullable=True),
)

cheques = Table(
    "dd_cashbox_cheque",
    metadata,
    Column("cheque_id", Integer, primary_key=True),
    Column("order_id", Integer),
    Column("cashbox_user_id", Integer),
    Column("cashbox_warehouse_id", Integer),
    Column("cashbox_requisites_id", Integer),
    Column("sum", Numeric(15, 4)),
    Column("checkbox_id", String(64)),
    Column("cashbox_work_shift_id", Integer),
    Column("cashbox_report_id", Integer),
    Column("date_added", DateTime),
)

z_reports = Table(
    "dd_cashbox_report",
    metadata,
    Column("report_id", Integer, primary_key=True),
    Column("user_id", Integer),
    Column("cashbox_user_id", Integer),
    Column("z_report_id", String(64)),
    Column("date_added", DateTime),
)

mapper = Base.registry.map_imperatively
mapper(Cashier, cashiers, properties={"id": cashiers.c.user_id, "is_active": cashiers.c.status})
mapper(CashboxWarehouse, cashbox_warehouses, properties={"id": cashbox_warehouses.c.warehouse_id})
mapper(CashboxRequisites, cashbox_requisites, properties={"id": cashbox_requisites.c.requisites_id})
mapper(
    WorkShift,
    work_shifts,
    properties={"id": work_shifts.c.work_shift_id, "cashier_id": work_shifts.c.cashbox_user_id},
)
mapper(
    Cheque,
    cheques,
    properties={
        "id": cheques.c.cheque_id,
        "cashier_id": cheques.c.cashbox_user_id,
        "warehouse_id": cheques.c.cashbox_warehouse_id,
        "requisites_id": cheques.c.cashbox_requisites_id,
        "work_shift_id": cheques.c.cashbox_work_shift_id,
        "report_id": cheques.c.cashbox_report_id,
    },
)
mapper(
    ZReport,
    z_reports,
    properties={
        "id": z_reports.c.report_id,
        "cashier_id": z_reports.c.cashbox_user_id,
        "checkbox_report_id": z_reports.c.z_report_id,
    },
)
