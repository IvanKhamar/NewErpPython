"""Checks NewErp against the real world it was written for: the old site's database and the outside services.

Many dd_* tables and columns were reconstructed from the old site's PHP code (db/reconstructed.sql), and the clients of
Nova Poshta, PrivatBank and LiqPay were written from their documentation. Only the owner has the real database and
keys, so this tool is run by the owner; nothing here writes anything.

    uv run python tools/check_live.py schema         # the NEWERP_DB_* database against db/reconstructed.sql
    uv run python tools/check_live.py integrations   # one read-only call to each service whose key is set

The keys come from the usual settings (env NEWERP_* or .env) and are never printed. Checkbox and SMSClub have no
read-only call (only receipts, shifts and SMS), so for them the tool only says whether they are configured.
The exit code is 1 when something differs or fails.
"""

import asyncio
import re
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from newerp.application.common import SystemClock
from newerp.application.delivery.models import TrackingRequest
from newerp.config import get_settings
from newerp.infrastructure.cashbox.checkbox import checkbox_client
from newerp.infrastructure.delivery.nova_poshta import nova_poshta_client
from newerp.infrastructure.payments import bank_statement_client, liqpay_gateway

RECONSTRUCTED = Path(__file__).resolve().parent.parent / "db" / "reconstructed.sql"


@dataclass(frozen=True)
class Column:
    table: str
    name: str
    type: str  # as in the DDL, lower case without the display width of integers: "int", "varchar(64)"


def _normal_type(definition: str) -> str:
    """The type part of a column definition, comparable with information_schema.COLUMN_TYPE."""
    type_ = definition.strip().split()[0].lower()
    return re.sub(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)", r"\1", type_)


def expected_columns(sql: str) -> list[Column]:
    """The tables and columns a DDL file creates or adds (CREATE TABLE … and ALTER TABLE … ADD COLUMN)."""
    columns: list[Column] = []
    for table, body in re.findall(r"CREATE TABLE `(\w+)` \((.*?)\n\)", sql, re.S):
        for name, definition in re.findall(r"^\s+`(\w+)` (.+?),?$", body, re.M):
            columns.append(Column(table, name, _normal_type(definition)))
    for table, body in re.findall(r"ALTER TABLE `(\w+)`(.*?);", sql, re.S):
        # The type may hold a comma itself: decimal(15,4).
        for name, definition in re.findall(r"ADD COLUMN `(\w+)` (\w+(?:\([^)]*\))?)", body):
            columns.append(Column(table, name, _normal_type(definition)))
    return columns


def compare(expected: list[Column], actual: dict[tuple[str, str], str]) -> list[str]:
    """Differences between the reconstruction and the real database (actual: (table, column) → COLUMN_TYPE)."""
    tables = {table for table, _ in actual}
    problems = []
    for column in expected:
        real = actual.get((column.table, column.name))
        if column.table not in tables:
            problems.append(f"немає таблиці {column.table}")
        elif real is None:
            problems.append(f"{column.table}: немає колонки {column.name} ({column.type})")
        elif _normal_type(real) != column.type:
            problems.append(f"{column.table}.{column.name}: у нас {column.type}, у базі {_normal_type(real)}")
    return list(dict.fromkeys(problems))


async def check_schema(database_url: str | None = None) -> list[str]:
    settings = get_settings()
    expected = expected_columns(RECONSTRUCTED.read_text(encoding="utf-8"))
    engine = create_async_engine(database_url or settings.database_url)
    try:
        async with engine.connect() as connection:
            rows = await connection.execute(
                text(
                    "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN :tables"
                ).bindparams(tables=tuple({c.table for c in expected})),
            )
            actual = {(table, column): type_ for table, column, type_ in rows}
    finally:
        await engine.dispose()
    print(f"Звірено {len(expected)} колонок у {len({c.table for c in expected})} таблицях.")
    return compare(expected, actual)


async def check_integrations() -> list[str]:
    settings = get_settings()
    problems = []
    today = SystemClock().now().date()

    async def probe(name: str, configured: bool, call) -> None:
        if not configured:
            print(f"{name}: ключ не задано, пропущено")
            return
        try:
            await call()
            print(f"{name}: відповідає")
        except Exception as error:  # noqa: BLE001 - any failure of an outside service is a finding to report
            problems.append(f"{name}: {type(error).__name__}: {error}")

    nova_poshta = nova_poshta_client(settings.nova_poshta)
    await probe("Нова Пошта (трекінг вигаданої ТТН)", nova_poshta.is_configured,
                lambda: nova_poshta.track([TrackingRequest("20450000000000", "")]))  # fmt: skip
    bank = bank_statement_client(settings.privat_bank)
    await probe("ПриватБанк (виписка за вчора й сьогодні)", bank.is_configured,
                lambda: bank.fetch(today - timedelta(days=1), today))  # fmt: skip
    liqpay = liqpay_gateway(settings.liq_pay)
    await probe("LiqPay (статус неіснуючого замовлення)", settings.liq_pay.is_configured,
                lambda: liqpay.status("0"))  # fmt: skip
    for name, configured in (
        ("Checkbox", not checkbox_client(settings.checkbox).is_fake),
        ("SMSClub", bool(settings.sms_club.token.get_secret_value())),
    ):
        state = "налаштовано" if configured else "ключ не задано"
        print(f"{name}: {state}; запиту без запису немає, перевірте тестовим чеком чи SMS вручну")
    return problems


def main(argv: list[str]) -> int:
    commands = {"schema": check_schema, "integrations": check_integrations}
    if len(argv) != 2 or argv[1] not in commands:
        print(__doc__)
        return 2
    problems = asyncio.run(commands[argv[1]]())
    for problem in problems:
        print(f"  ✗ {problem}")
    print("Розбіжностей немає." if not problems else f"Знайдено: {len(problems)}.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
