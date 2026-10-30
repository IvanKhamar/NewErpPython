"""Consumables stock (the old site's StockTrack module): items, movement log, warehouses, stocktaking, purchase orders.

These are plain classes; infrastructure maps them to tables with SQLAlchemy imperative mapping (like EF fluent
configuration), so the domain does not know about the database.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from newerp.domain.common import DomainError, plain, require_text

USE = "use"  # consumption; the old module writes the same
RESTOCK = "restock"  # receipt

SCALE = 3  # quantities are decimal(12,3)


def has_scale(quantity: Decimal, places: int = SCALE) -> bool:
    return quantity.quantize(Decimal(1).scaleb(-places), ROUND_HALF_EVEN) == quantity


class StockCategory:
    def __init__(self, name: str | None, sort_order: int = 0) -> None:
        self.id: int | None = None
        self.name = require_text(name, "Назва категорії", 100)
        self.sort_order = sort_order


class StockSupplier:
    def __init__(
        self, name: str | None, phone: str | None, email: str | None, categories: str | None, notes: str | None
    ) -> None:
        self.id: int | None = None
        self.name = require_text(name, "Назва постачальника", 150)
        # The old module stores an empty string, not NULL.
        self.phone = (phone or "").strip()
        self.email = (email or "").strip()
        self.categories = (categories or "").strip()  # free text: what they supply
        self.notes = (notes or "").strip()


class StockItem:
    """
    A consumable (fabric, fittings, packaging...), the old site's dd_stock_items. The quantity changes only
    through adjust, take_up_to and recount, so every change gets into the movement log.
    """

    def __init__(
        self,
        category_id: int | None,
        supplier_id: int | None,
        name: str | None,
        unit: str | None,
        min_quantity: Decimal,
        used_for: str | None = None,
        price: Decimal = Decimal(0),
    ) -> None:
        self.id: int | None = None
        self.quantity = Decimal(0)
        self.is_active = True  # False: "deleted"; the old module sets status = 0 instead of deleting
        self.update(category_id, supplier_id, name, unit, min_quantity, used_for, price)

    def update(
        self,
        category_id: int | None,
        supplier_id: int | None,
        name: str | None,
        unit: str | None,
        min_quantity: Decimal,
        used_for: str | None,
        price: Decimal,
    ) -> None:
        """Changes the description. Never the quantity: that goes only through the log."""
        if price < 0:
            raise DomainError("Ціна не може бути від'ємною.")
        self.category_id = category_id
        self.supplier_id = supplier_id
        self.name = require_text(name, "Назва позиції", 150)
        self.unit = require_text(unit, "Одиниця виміру", 16)
        self.used_for = (used_for or "").strip()
        self.price = price
        self.set_min_quantity(min_quantity)

    @property
    def is_below_minimum(self) -> bool:
        return self.quantity < self.min_quantity

    def adjust(
        self, delta: Decimal, comment: str | None, user_id: int, username: str, now: datetime
    ) -> "StockMovement":
        """A manual change by delta (+ receipt, − use). Stricter than the old module: no going below zero."""
        if delta == 0:
            raise DomainError("Зміна залишку не може дорівнювати нулю.")
        if self.quantity + delta < 0:
            raise DomainError(f"Недостатньо залишку: є {self.quantity} {self.unit}, списується {-delta} {self.unit}.")
        return self._change(delta, require_text(comment, "Причина", 255), user_id, username, now)

    def take_up_to(
        self, wanted: Decimal, comment: str | None, user_id: int, username: str, now: datetime
    ) -> "StockMovement | None":
        """Write-off for production, as in the old module: takes what there is, at most down to zero."""
        if wanted <= 0:
            raise DomainError("Кількість для списання має бути більшою за нуль.")
        amount = min(wanted, self.quantity)
        if amount <= 0:
            return None
        return self._change(-amount, require_text(comment, "Причина", 255), user_id, username, now)

    def recount(
        self, actual: Decimal, comment: str | None, user_id: int, username: str, now: datetime
    ) -> "StockMovement | None":
        """Stocktaking: the quantity becomes the counted one, the difference goes into the log."""
        require_valid_count(actual)
        delta = actual - self.quantity
        if delta == 0:
            return None
        return self._change(delta, require_text(comment, "Причина", 255), user_id, username, now)

    def set_min_quantity(self, min_quantity: Decimal) -> None:
        if min_quantity < 0:
            raise DomainError("Мінімальний залишок не може бути від'ємним.")
        self.min_quantity = min_quantity

    def _change(self, delta: Decimal, comment: str, user_id: int, username: str, now: datetime) -> "StockMovement":
        before = self.quantity
        self.quantity += delta
        return StockMovement(self, delta, before, comment, user_id, username, now)


class StockMovement:
    """A row of the movement log (dd_stock_log). Made only by StockItem."""

    def __init__(
        self,
        item: StockItem,
        delta: Decimal,
        stock_before: Decimal,
        comment: str,
        user_id: int,
        username: str,
        created_at: datetime,
    ) -> None:
        self.id: int | None = None
        self.item = item
        self.item_id = item.id
        self.item_name = item.name  # the old log keeps a copy of the name
        self.action = USE if delta < 0 else RESTOCK
        self.quantity = abs(delta)  # always positive
        self.stock_before = stock_before
        self.stock_after = item.quantity
        self.comment = comment
        self.user_id = user_id
        self.username = username
        self.created_at = created_at

    @property
    def delta(self) -> Decimal:
        """Signed change: minus is use, plus is receipt."""
        return -self.quantity if self.action == USE else self.quantity


class StockBalance:
    """A consumable's quantity at one warehouse (erp_stock_location). Changes only through StockPlacement."""

    def __init__(self, item_id: int, warehouse_id: int, quantity: Decimal = Decimal(0)) -> None:
        self.id: int | None = None
        self.item_id = item_id
        self.warehouse_id = warehouse_id
        self.quantity = quantity


class StockTransfer:
    """A move between warehouses (erp_stock_transfer); None means the main warehouse. Not in the movement log."""

    def __init__(
        self,
        item_id: int,
        from_warehouse_id: int | None,
        to_warehouse_id: int | None,
        quantity: Decimal,
        comment: str,
        user_id: int,
        username: str,
        created_at: datetime,
    ) -> None:
        self.id: int | None = None
        self.item_id = item_id
        self.from_warehouse_id = from_warehouse_id
        self.to_warehouse_id = to_warehouse_id
        self.quantity = quantity
        self.comment = comment
        self.user_id = user_id
        self.username = username
        self.created_at = created_at


class StockPlacement:
    """
    Where a consumable lies. dd_stock_items.stock stays the total: the old site, orders and production use it.
    Warehouses have their own balances, and the main warehouse is the rest (total minus all warehouses).
    So whatever names no warehouse takes from or adds to the main one. warehouse_id None is the main warehouse.
    """

    def __init__(self, item: StockItem, balances: Iterable[StockBalance]) -> None:
        self.item = item
        self._balances = {b.warehouse_id: b for b in balances}
        self.created: list[StockBalance] = []
        """Balances for warehouses where the item was not yet: they must be added to the session."""

    @property
    def balances(self) -> list[StockBalance]:
        return list(self._balances.values())

    @property
    def main(self) -> Decimal:
        """The main warehouse: total minus warehouses. Negative if the total was lowered past it."""
        return self.item.quantity - self._located

    @property
    def _located(self) -> Decimal:
        return sum((b.quantity for b in self._balances.values()), Decimal(0))

    def at(self, warehouse_id: int | None) -> Decimal:
        if warehouse_id is None:
            return self.main
        balance = self._balances.get(warehouse_id)
        return balance.quantity if balance else Decimal(0)

    def move(
        self,
        from_id: int | None,
        to_id: int | None,
        quantity: Decimal,
        comment: str | None,
        user_id: int,
        username: str,
        now: datetime,
    ) -> StockTransfer:
        """Moves a quantity between two places; the total stays. Not more than the source has."""
        if from_id == to_id:
            raise DomainError("Звідки й куди переміщують — має бути різне місце.")
        require_quantity(quantity)
        text = (comment or "").strip()
        if len(text) > 255:
            raise DomainError("Коментар: не довше 255 символів.")
        self._require_available(from_id, quantity)
        self._change(from_id, -quantity)
        self._change(to_id, quantity)
        return StockTransfer(self.item.id, from_id, to_id, quantity, text, user_id, username, now)

    def adjust_at(
        self, warehouse_id: int | None, delta: Decimal, comment: str | None, user_id: int, username: str, now: datetime
    ) -> StockMovement:
        """Receipt (+) or use (−) in one place; the total changes too, so it goes into the movement log."""
        # With nothing at the warehouses the main one is the total: the item's own check applies.
        if delta < 0 and (warehouse_id is not None or self._located != 0):
            self._require_available(warehouse_id, -delta)
        movement = self.item.adjust(delta, comment, user_id, username, now)
        self._change(warehouse_id, delta)
        return movement

    def recount_at(
        self, warehouse_id: int | None, actual: Decimal, comment: str, user_id: int, username: str, now: datetime
    ) -> StockMovement | None:
        """Stocktaking of one place: it gets the counted quantity, the total changes by the same difference."""
        require_valid_count(actual)
        delta = actual - self.at(warehouse_id)
        if delta == 0:
            return None
        movement = self.item.recount(self.item.quantity + delta, comment, user_id, username, now)
        self._change(warehouse_id, delta)
        return movement

    def received_at(self, warehouse_id: int, quantity: Decimal) -> None:
        """A receipt already in the total (a delivery) lies at this warehouse, not at the main one."""
        require_quantity(quantity)
        self._change(warehouse_id, quantity)

    def _change(self, warehouse_id: int | None, delta: Decimal) -> None:
        if warehouse_id is None:
            return  # the main warehouse is the rest; it changes by itself
        balance = self._balances.get(warehouse_id)
        if balance is None:
            balance = StockBalance(self.item.id, warehouse_id)
            self._balances[warehouse_id] = balance
            self.created.append(balance)
        balance.quantity += delta

    def _require_available(self, warehouse_id: int | None, quantity: Decimal) -> None:
        available = max(self.at(warehouse_id), Decimal(0))
        if available < quantity:
            place = "на основному складі" if warehouse_id is None else "на цьому складі"
            unit = self.item.unit
            raise DomainError(
                f"Недостатньо «{self.item.name}»: {place} є {plain(available, 3)} {unit}, "
                f"потрібно {plain(quantity, 3)} {unit}."
            )


def require_quantity(quantity: Decimal) -> None:
    if quantity <= 0 or not has_scale(quantity):
        raise DomainError("Кількість має бути більшою за нуль, не більше 3 знаків після коми.")


# ── Stocktaking: the storekeeper downloads the list, writes the counts into the last column and uploads it ──

STOCKTAKING_COMMENT = "Інвентаризація"  # reports tell corrections from real use by this comment
STOCKTAKING_COLUMNS = ("ID", "Назва", "Категорія", "Од.", "За обліком", "Фактично")


@dataclass(frozen=True)
class Count:
    item_id: int
    actual: Decimal


def is_valid_count(quantity: Decimal) -> bool:
    return quantity >= 0 and has_scale(quantity)


def require_valid_count(actual: Decimal) -> None:
    if not is_valid_count(actual):
        raise DomainError(f"Фактична кількість має бути від 0, не більше {SCALE} знаків після коми.")


def stocktaking_comment(note: str | None) -> str:
    """ "Інвентаризація" or "Інвентаризація: note", cut to the 255 characters of the log column."""
    text = STOCKTAKING_COMMENT if not (note or "").strip() else f"{STOCKTAKING_COMMENT}: {note.strip()}"
    return text[:255]


def parse_stocktaking(rows: Sequence[Sequence[str]]) -> tuple[list[Count], list[str]]:
    """
    Reads the sheet: the ID column and the last ("Фактично"). A row with an empty count was not counted and is
    skipped; so is the header row. Errors name the row number as Excel shows it.
    """
    counts: list[Count] = []
    errors: list[str] = []
    seen: set[int] = set()
    actual_column = len(STOCKTAKING_COLUMNS) - 1

    def cell(row: Sequence[str], index: int) -> str:
        return (row[index] or "").strip() if index < len(row) else ""

    for r, row in enumerate(rows):
        item_text, actual_text = cell(row, 0), cell(row, actual_column)
        if (r == 0 and item_text.casefold() == "id") or not actual_text:
            continue
        line = r + 1
        quantity = _parse_quantity(actual_text)
        if not item_text.isdigit() or int(item_text) <= 0:
            errors.append(f"Рядок {line}: немає ID позиції або він не число.")
        elif quantity is None:
            errors.append(f"Рядок {line}: «{actual_text}» — не кількість.")
        elif not is_valid_count(quantity):
            errors.append(f"Рядок {line}: кількість має бути від 0, не більше {SCALE} знаків після коми.")
        elif int(item_text) in seen:
            errors.append(f"Рядок {line}: позиція {int(item_text)} указана двічі.")
        else:
            seen.add(int(item_text))
            counts.append(Count(int(item_text), quantity))
    return counts, errors


def _parse_quantity(text: str) -> Decimal | None:
    """Excel in Ukrainian shows "12,5", and spaces separate thousands ("1 250")."""
    cleaned = text.replace(",", ".").replace(" ", "").replace(" ", "")
    if not cleaned or not all(c.isdigit() or c in "+-." for c in cleaned):
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


# ── Purchase orders: draft → ordered → received (or cancelled) ──

DRAFT, ORDERED, RECEIVED, CANCELLED = "draft", "ordered", "received", "cancelled"


class PurchaseOrderLine:
    def __init__(self, item_id: int, quantity: Decimal, price: Decimal) -> None:
        self.id: int | None = None
        self.item_id = item_id
        self.quantity = quantity
        self.price = price
        self.received_quantity = Decimal(0)

    @property
    def remaining(self) -> Decimal:
        """How much is still expected (0 when all or more has come)."""
        return max(Decimal(0), self.quantity - self.received_quantity)


class PurchaseOrder:
    """An order to a consumables supplier. Can be received in parts; every receipt is a receipt in the stock log."""

    def __init__(
        self,
        supplier_id: int,
        lines: Sequence[tuple[int, Decimal, Decimal]],
        comment: str | None,
        user_id: int,
        now: datetime,
    ) -> None:
        self.id: int | None = None
        self.supplier_id = supplier_id
        self.status = DRAFT
        self.comment = _comment_text(comment)
        self.created_by_user_id = user_id
        self.created_at = now
        self.ordered_at: datetime | None = None
        self.closed_at: datetime | None = None  # when fully received or cancelled
        self._lines: list[PurchaseOrderLine] = []
        self.set_lines(lines)

    @property
    def lines(self) -> list[PurchaseOrderLine]:
        return list(self._lines)

    @property
    def is_open(self) -> bool:
        """Still waiting for goods (a draft too: it will be sent soon)."""
        return self.status in (DRAFT, ORDERED)

    def set_lines(self, lines: Sequence[tuple[int, Decimal, Decimal]]) -> None:
        """Replaces the lines. Only in a draft: an order sent to the supplier is not changed."""
        self._require_status(DRAFT, "змінювати рядки")
        if not lines:
            raise DomainError("Замовлення має містити хоча б одну позицію.")
        if any(quantity <= 0 for _, quantity, _ in lines):
            raise DomainError("Кількість у замовленні має бути більшою за нуль.")
        if any(price < 0 for _, _, price in lines):
            raise DomainError("Ціна не може бути від'ємною.")
        duplicate = _first_duplicate(item_id for item_id, _, _ in lines)
        if duplicate is not None:
            raise DomainError(f"Позицію {duplicate} указано двічі.")
        self._lines.clear()
        self._lines.extend(PurchaseOrderLine(item_id, quantity, price) for item_id, quantity, price in lines)

    def mark_ordered(self, now: datetime) -> None:
        self._require_status(DRAFT, "позначити як замовлене")
        self.status = ORDERED
        self.ordered_at = now

    def cancel(self, now: datetime) -> None:
        """A draft or an ordered one can be cancelled (what has been received stays in stock)."""
        if not self.is_open:
            raise DomainError(f"Замовлення №{self.id} вже закрите.")
        self.status = CANCELLED
        self.closed_at = now

    def receive(
        self,
        receipts: Sequence[tuple[int, Decimal]],
        items: Mapping[int, StockItem],
        user_id: int,
        username: str,
        now: datetime,
    ) -> list[StockMovement]:
        """
        Every receipt line raises the consumable's quantity ("Прихід за замовленням…" in the log). More than
        ordered is allowed: suppliers sometimes add. When everything has come, the order closes.
        """
        self._require_status(ORDERED, "приймати товар")
        if not receipts:
            raise DomainError("Вкажіть, що саме отримано.")
        duplicate = _first_duplicate(line_id for line_id, _ in receipts)
        if duplicate is not None:
            raise DomainError(f"Рядок {duplicate} указано двічі.")

        movements = []
        for line_id, quantity in receipts:
            line = next((line for line in self._lines if line.id == line_id), None)
            if line is None:
                raise DomainError(f"Рядок {line_id} не належить замовленню №{self.id}.")
            if quantity <= 0:
                raise DomainError("Отримана кількість має бути більшою за нуль.")
            item = items.get(line.item_id)
            if item is None:
                raise DomainError(f"Розхідника {line.item_id} немає на складі.")
            line.received_quantity += quantity
            movements.append(
                item.adjust(quantity, f"Прихід за замовленням постачальнику №{self.id}", user_id, username, now)
            )

        if all(line.remaining == 0 for line in self._lines):
            self.status = RECEIVED
            self.closed_at = now
        return movements

    def _require_status(self, status: str, action: str) -> None:
        if self.status != status:
            raise DomainError(f"Замовлення №{self.id} у статусі {self.status}: {action} не можна.")


def _comment_text(comment: str | None) -> str:
    text = (comment or "").strip()
    if len(text) > 500:
        raise DomainError("Коментар: не довше 500 символів.")
    return text


def _first_duplicate(values: Iterable[int]) -> int | None:
    seen: set[int] = set()
    for value in values:
        if value in seen:
            return value
        seen.add(value)
    return None
