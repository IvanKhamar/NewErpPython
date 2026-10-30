"""A shop's warehouse or pickup point (dd_warehouse), like "Каталог → Склади" of the old admin."""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from newerp.domain.common import DomainError


@dataclass(frozen=True)
class ShopLocationData:
    """Fields of a warehouse or pickup point, already trimmed."""

    name: str
    description: str
    contact: str
    telephone: str
    city: str
    address: str
    type: str


@dataclass(frozen=True)
class LocationImportRow:
    """A row of the import file: id 0 is a new record, otherwise an update of an existing one."""

    row_number: int
    id: int
    data: ShopLocationData


# Excel columns in the same order as the old site's export.
COLUMNS = ("ID", "Назва", "Опис", "Контакт", "Телефон", "Місто", "Адреса", "Тип")


def validate(
    name: str | None,
    description: str | None,
    contact: str | None,
    telephone: str | None,
    city: str | None,
    address: str | None,
    type_: str | None,
) -> ShopLocationData:
    """The name is required, up to 64 characters as in the old site's form; the rest fit their columns."""
    data = ShopLocationData(
        *((value or "").strip() for value in (name, description, contact, telephone, city, address, type_))
    )
    if not 1 <= len(data.name) <= 64:
        raise DomainError("Назва має містити від 1 до 64 символів.")
    for value, limit, field in (
        (data.contact, 128, "Контакт"),
        (data.telephone, 255, "Телефон"),
        (data.city, 128, "Місто"),
        (data.address, 255, "Адреса"),
        (data.type, 64, "Тип"),
        (data.description, 5000, "Опис"),
    ):
        if len(value) > limit:
            raise DomainError(f"{field} довший за {limit} символів.")
    return data


def parse_import(rows: Sequence[Sequence[str]]) -> tuple[list[LocationImportRow], list[str]]:
    """
    Reads the file rows (the first is the header, empty ones are skipped). The old import wrote every row at once
    without checks, so an error in the middle left half of the changes. Here the errors of the whole file are
    collected, and with any error nothing is written.
    """
    result: list[LocationImportRow] = []
    errors: list[str] = []
    for i in range(1, len(rows)):
        cells = rows[i]

        def cell(index: int, cells: Sequence[str] = cells) -> str:
            return (cells[index] or "") if index < len(cells) else ""

        if all(not cell(c).strip() for c in range(len(COLUMNS))):
            continue
        row_number = i + 1
        id_text = cell(0).strip()
        if id_text and (not re.fullmatch(r"[+-]?\d+", id_text) or int(id_text) < 0):
            errors.append(f"Рядок {row_number}: ID «{id_text}» має бути числом або порожнім.")
            continue
        try:
            data = validate(cell(1), cell(2), cell(3), cell(4), cell(5), cell(6), cell(7))
            result.append(LocationImportRow(row_number, int(id_text) if id_text else 0, data))
        except DomainError as error:
            errors.append(f"Рядок {row_number}: {error}")

    by_id: dict[int, list[int]] = {}
    for row in result:
        if row.id > 0:
            by_id.setdefault(row.id, []).append(row.row_number)
    for location_id, row_numbers in by_id.items():
        if len(row_numbers) > 1:
            errors.append(f"ID {location_id} повторюється в рядках {', '.join(map(str, row_numbers))}.")
    if not result and not errors:
        errors.append("У файлі немає рядків для імпорту.")
    return result, errors
