"""Mass price changes (item 46) and the CSV import of products (item 44), both with a preview before saving.

The old price module and importer built SQL from request values and downloaded pictures from any URL in the file;
here every value is checked first, the file is parsed in memory only, and nothing is fetched from the network."""

import csv
import io
import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal, Protocol

from pydantic import Field

from newerp.application.common import Dto, Money
from newerp.domain.catalog import PriceOperation, PriceUnit, changed_price, sale_percent
from newerp.domain.common import DomainError

audit = logging.getLogger("newerp.audit")

PREVIEW_ROWS = 500


# ── Mass price changes ──


class PriceChangeRequest(Dto):
    """target price: the regular price; sale: the sale percent (set) or the sale itself (remove), customer group 1.
    Products: of any of the categories and of any of the manufacturers; all_products must be said out loud."""

    target: Literal["price", "sale"] = "price"
    operation: PriceOperation | Literal["set_percent", "remove"] = PriceOperation.ADD
    amount: Decimal = Field(default=Decimal(0), ge=0, le=1_000_000)
    unit: PriceUnit = PriceUnit.AMOUNT
    category_ids: list[int] = Field(default_factory=list, max_length=500)
    manufacturer_ids: list[int] = Field(default_factory=list, max_length=500)
    all_products: bool = False


@dataclass(frozen=True)
class PricedProduct:
    product_id: int
    name: str
    model: str
    price: Decimal
    sale_percent: Decimal | None  # the percent of its group-1 sale, None without one


class PriceChangeRow(Dto):
    """old/new: prices for target price, sale percents for target sale (None: no sale)."""

    product_id: int
    name: str
    model: str
    old: Money | None
    new: Money | None


class PriceChangePreview(Dto):
    total: int
    changed: int
    rows: list[PriceChangeRow]  # the first PREVIEW_ROWS changed ones


class PriceChangeResult(Dto):
    changed: int


class PriceStore(Protocol):
    async def products(self, category_ids: list[int], manufacturer_ids: list[int]) -> list[PricedProduct]:
        """Empty lists mean no limit by that list."""
        ...

    async def set_prices(self, prices: dict[int, Decimal]) -> None: ...

    async def set_sale_percents(self, product_ids: list[int], percent: Decimal) -> None:
        """Group-1 sales become this percent; a product without one gets a new unlimited sale."""
        ...

    async def remove_sales(self, product_ids: list[int]) -> None: ...


class PriceChangeService:
    def __init__(self, store: PriceStore) -> None:
        self._store = store

    async def preview(self, request: PriceChangeRequest) -> PriceChangePreview:
        products, rows = await self._plan(request)
        return PriceChangePreview(total=len(products), changed=len(rows), rows=rows[:PREVIEW_ROWS])

    async def apply(self, request: PriceChangeRequest, user_id: int) -> PriceChangeResult:
        _, rows = await self._plan(request)
        if not rows:
            return PriceChangeResult(changed=0)
        ids = [row.product_id for row in rows]
        if request.target == "price":
            await self._store.set_prices({row.product_id: row.new for row in rows if row.new is not None})
        elif request.operation == "remove":
            await self._store.remove_sales(ids)
        else:
            await self._store.set_sale_percents(ids, request.amount)
        for row in rows:
            audit.info("mass %s change: product %s, %s -> %s, by user %s",
                       request.target, row.product_id, row.old, row.new, user_id)  # fmt: skip
        return PriceChangeResult(changed=len(rows))

    async def _plan(self, request: PriceChangeRequest) -> tuple[list[PricedProduct], list[PriceChangeRow]]:
        if not request.category_ids and not request.manufacturer_ids and not request.all_products:
            raise DomainError("Оберіть категорії чи виробників або явно позначте «Усі товари».")
        if request.target == "price" and request.operation not in tuple(PriceOperation):
            raise DomainError("Для ціни: додати, відняти, помножити або поділити.")
        if request.target == "sale":
            if request.operation not in ("set_percent", "remove"):
                raise DomainError("Для акції: встановити відсоток або прибрати акцію.")
            if request.operation == "set_percent":
                sale_percent(request.amount)
        products = await self._store.products(request.category_ids, request.manufacturer_ids)
        rows: list[PriceChangeRow] = []
        for p in products:
            if request.target == "price":
                old, new = (
                    p.price,
                    changed_price(p.price, PriceOperation(request.operation), request.amount, request.unit),
                )
            else:
                old, new = p.sale_percent, (None if request.operation == "remove" else request.amount)
            if old != new:
                rows.append(PriceChangeRow(product_id=p.product_id, name=p.name, model=p.model, old=old, new=new))
        return products, rows


# ── CSV import ──

MAX_IMPORT_BYTES = 1_000_000
MAX_IMPORT_ROWS = 5_000
KEY_COLUMNS = ("product_id", "model")
DECIMAL_COLUMNS = ("price", "weight", "length", "width", "height")
INT_COLUMNS = ("sort_order", "status")
TEXT_COLUMNS = {"sku": 64, "name": 255}
IMPORT_COLUMNS = (*KEY_COLUMNS, *DECIMAL_COLUMNS, *INT_COLUMNS, *TEXT_COLUMNS)


class ImportRowDto(Dto):
    line: int
    product_id: int | None
    model: str
    changes: dict[str, str]  # column → "old → new"
    error: str | None = None


class ImportPreview(Dto):
    rows: list[ImportRowDto]  # with changes or an error; unchanged rows are left out
    unchanged: int
    errors: int


class ImportResult(Dto):
    updated: int
    errors: int


@dataclass(frozen=True)
class ImportedProduct:
    product_id: int
    model: str
    values: dict[str, object]  # the current value of every import column but the keys


class ImportStore(Protocol):
    async def find(self, product_ids: list[int], models: list[str]) -> list[ImportedProduct]: ...

    async def update(self, product_id: int, values: dict[str, object]) -> None:
        """dd_product columns and the name; only the given ones."""
        ...


class ProductImportService:
    def __init__(self, store: ImportStore) -> None:
        self._store = store

    async def preview(self, content: bytes) -> ImportPreview:
        rows, _ = await self._plan(content)
        shown = [row for row, _ in rows if row.changes or row.error]
        return ImportPreview(
            rows=shown, unchanged=len(rows) - len(shown), errors=sum(1 for row, _ in rows if row.error)
        )

    async def apply(self, content: bytes, user_id: int) -> ImportResult:
        """Only rows without an error are written; the rest are reported, as in the preview."""
        rows, _ = await self._plan(content)
        updated = 0
        for row, values in rows:
            if row.error or not values or row.product_id is None:
                continue
            await self._store.update(row.product_id, values)
            audit.info("product imported: product %s, %s, by user %s", row.product_id, row.changes, user_id)
            updated += 1
        return ImportResult(updated=updated, errors=sum(1 for row, _ in rows if row.error))

    async def _plan(self, content: bytes) -> tuple[list[tuple[ImportRowDto, dict[str, object]]], list[str]]:
        header, records = parse_csv(content)
        ids = [int(r["product_id"]) for _, r in records if r.get("product_id", "").isdigit()]
        models = [r["model"].strip() for _, r in records if r.get("model", "").strip()]
        found = await self._store.find(ids, models)
        by_id = {p.product_id: p for p in found}
        by_model: dict[str, list[ImportedProduct]] = {}
        for p in found:
            by_model.setdefault(p.model, []).append(p)

        planned: list[tuple[ImportRowDto, dict[str, object]]] = []
        for line, record in records:
            key_id = record.get("product_id", "").strip()
            model = record.get("model", "").strip()
            row = ImportRowDto(line=line, product_id=None, model=model, changes={})
            try:
                if key_id:
                    if not key_id.isdigit() or int(key_id) not in by_id:
                        raise DomainError(f"товару №{key_id} немає")
                    product = by_id[int(key_id)]
                else:
                    matches = by_model.get(model, [])
                    if not matches:
                        raise DomainError(f"товару з моделлю «{model}» немає (імпорт лише оновлює товари)")
                    if len(matches) > 1:
                        raise DomainError(f"модель «{model}» мають кілька товарів: вкажіть product_id")
                    product = matches[0]
                row.product_id, row.model = product.product_id, product.model
                values = {
                    column: new
                    for column in header
                    if column not in KEY_COLUMNS
                    and (new := _value(column, record[column])) is not None
                    and new != product.values[column]
                }
                row.changes = {column: f"{product.values[column]} → {value}" for column, value in values.items()}
            except DomainError as error:
                row.error = str(error)
                values = {}
            planned.append((row, values))
        return planned, header


def parse_csv(content: bytes) -> tuple[list[str], list[tuple[int, dict[str, str]]]]:
    """UTF-8 (with or without BOM), ";" or "," between columns, the first line names them."""
    if len(content) > MAX_IMPORT_BYTES:
        raise DomainError(f"Файл більший за {MAX_IMPORT_BYTES // 1_000_000} МБ.")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise DomainError("Файл має бути в кодуванні UTF-8.") from error
    first = text.split("\n", 1)[0]
    delimiter = ";" if first.count(";") >= first.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header = [name.strip().lower() for name in next(reader)]
    except StopIteration as error:
        raise DomainError("Файл порожній.") from error
    unknown = [name for name in header if name not in IMPORT_COLUMNS]
    if unknown:
        raise DomainError(f"Невідомі колонки: {', '.join(unknown)}. Можна: {', '.join(IMPORT_COLUMNS)}.")
    if not any(key in header for key in KEY_COLUMNS) or len(set(header)) != len(header):
        raise DomainError("Потрібна колонка model або product_id, і кожна колонка лише раз.")
    records: list[tuple[int, dict[str, str]]] = []
    for line, cells in enumerate(reader, start=2):
        if not any(cell.strip() for cell in cells):
            continue
        if len(records) >= MAX_IMPORT_ROWS:
            raise DomainError(f"Більше за {MAX_IMPORT_ROWS} рядків: розбийте файл.")
        records.append((line, dict(zip(header, (*cells, *[""] * len(header)), strict=False))))
    return header, records


def _value(column: str, cell: str) -> object | None:
    """The cell as the column's type; an empty cell keeps the value (None)."""
    text = cell.strip()
    if not text:
        return None
    if column in DECIMAL_COLUMNS:
        try:
            number = Decimal(text.replace(",", ".").replace(" ", ""))
        except InvalidOperation as error:
            raise DomainError(f"{column}: «{text}» не число") from error
        if not number.is_finite() or number < 0 or number > 10_000_000:
            raise DomainError(f"{column}: «{text}» поза межами")
        return number
    if column in INT_COLUMNS:
        if not text.lstrip("-").isdigit():
            raise DomainError(f"{column}: «{text}» не ціле число")
        number = int(text)
        if column == "status" and number not in (0, 1):
            raise DomainError("status: 1 — увімкнений, 0 — вимкнений")
        return number
    if len(text) > TEXT_COLUMNS[column]:
        raise DomainError(f"{column}: довше за {TEXT_COLUMNS[column]} символів")
    return text
