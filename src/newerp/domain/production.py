"""Production (the old site's StockTrack module): models with sizes, cut and sewn stock, BOMs, sewing plan, sewn
pieces at warehouses. Plain classes; infrastructure maps them to tables (see infrastructure/production/mapping.py)."""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal

from newerp.domain.common import DomainError, plain, require_text
from newerp.domain.stock import has_scale

CLOTHING = "clothing"  # own sewing: cut → sewn
READY_MADE = "ready_made"  # ready goods from a supplier: only "sewn" stock

CUT, SEWN = "cut", "sewn"  # log stages, as the old module writes them
IN, OUT = "in", "out"  # log directions


def order_comment(order_id: int) -> str:
    """The log comment the old site uses when an order takes sewn pieces."""
    return f"Замовлення #{order_id}"


def cancel_comment(order_id: int) -> str:
    """The log comment for pieces returned when an order leaves the write-off statuses."""
    return f"Скасування #{order_id}"


class ProductionSize:
    """A size of a model with its own cut and sewn stock (dd_stock_production_sizes)."""

    def __init__(self, name: str, percent: Decimal, sort_order: int) -> None:
        self.id: int | None = None
        self.name = name
        self.percent = percent  # share of the size in a batch, %
        self.cut_stock = Decimal(0)  # cut but not yet sewn
        self.sewn_stock = Decimal(0)  # sewn and ready to sell
        self.sort_order = sort_order
        self.option_value_id: int | None = None  # the OpenCart option value (size in the product card) it is linked to


class ProductionProductLink:
    """A store product linked to the model directly (dd_stock_production_products)."""

    def __init__(self, product_id: int) -> None:
        self.product_id = product_id


class ProductionCategoryLink:
    """
    A link through an OpenCart category (dd_stock_production_categories): products of the category whose name
    contains name_filter (or equals it with match_exact). An empty filter is the whole category.
    """

    def __init__(self, category_id: int, name_filter: str, match_exact: bool) -> None:
        self.category_id = category_id
        self.name_filter = name_filter
        self.match_exact = match_exact


class SewnBomLine:
    """
    A sewing BOM row (dd_stock_production_sewn_bom): one sewn piece takes either a cut part (component_id) or a
    consumable directly (item_id: threads, labels). size_id None: for all sizes; a row for a size overrides it.
    """

    def __init__(self, size_id: int | None, component_id: int | None, item_id: int | None, qty_per_unit: Decimal):
        self.id: int | None = None
        self.size_id = size_id
        self.component_id = component_id
        self.item_id = item_id
        self.qty_per_unit = qty_per_unit


class ComponentBomLine:
    """A cutting BOM row (dd_stock_production_component_bom): how much of a consumable one cut part takes."""

    def __init__(self, size_id: int | None, item_id: int, qty_per_unit: Decimal) -> None:
        self.id: int | None = None
        self.size_id = size_id
        self.item_id = item_id
        self.qty_per_unit = qty_per_unit


class ProductionLogEntry:
    """A production log row (dd_stock_production_log): a change of one size's cut or sewn stock."""

    def __init__(
        self,
        model: "ProductionModel",
        size: ProductionSize,
        stage: str,
        delta: Decimal,
        before: Decimal,
        after: Decimal,
        comment: str,
        user_id: int,
        username: str,
        created_at: datetime,
    ) -> None:
        self.id: int | None = None
        # The references let the ORM fill in the ids of a new model or size when saving.
        self.model = model
        self.size = size
        self.production_id = model.id
        self.size_id = size.id
        self.size_name = size.name
        self.stage = stage
        self.direction = IN if delta > 0 else OUT
        self.quantity = abs(delta)
        self.stock_before = before
        self.stock_after = after
        self.comment = comment
        self.user_id = user_id
        self.username = username
        self.created_at = created_at


class ProductionModel:
    """A model (dd_stock_production): sizes with cut and sewn stock, links to store products and the sewing BOM."""

    def __init__(self, name: str | None, type_: str | None, min_total: int, sort_order: int) -> None:
        self.id: int | None = None
        self.is_active = True  # False: "deleted"; the old module sets status = 0
        self._sizes: list[ProductionSize] = []
        self._product_links: list[ProductionProductLink] = []
        self._category_links: list[ProductionCategoryLink] = []
        self._sewn_bom: list[SewnBomLine] = []
        self.update(name, type_, min_total, sort_order)

    @property
    def sizes(self) -> list[ProductionSize]:
        return list(self._sizes)

    @property
    def product_links(self) -> list[ProductionProductLink]:
        return list(self._product_links)

    @property
    def category_links(self) -> list[ProductionCategoryLink]:
        return list(self._category_links)

    @property
    def sewn_bom(self) -> list[SewnBomLine]:
        return list(self._sewn_bom)

    def update(self, name: str | None, type_: str | None, min_total: int, sort_order: int) -> None:
        if type_ not in (CLOTHING, READY_MADE):
            raise DomainError(f"Тип моделі має бути {CLOTHING} або {READY_MADE}.")
        if min_total < 0:
            raise DomainError("Мінімальний запас не може бути від'ємним.")
        self.name = require_text(name, "Назва моделі", 150)
        self.type = type_
        self.min_total = min_total  # with fewer sewn pieces in total the model is in the "to sew" list
        self.sort_order = sort_order

    def deactivate(self) -> None:
        self.is_active = False

    def set_sizes(self, sizes: Sequence[tuple[str | None, Decimal]]) -> list[int]:
        """
        Sets the list of sizes, matched by name: existing ones keep their id and stock, new ones are added, and only
        a size without stock and without sewing BOM rows can be removed. (The old module recreated all sizes, so
        their ids changed.) Returns the ids of removed sizes, so the caller checks nothing else refers to them.
        """
        names = [require_text(name, "Назва розміру", 32) for name, _ in sizes]
        seen: set[str] = set()
        for name in names:
            if name.casefold() in seen:
                raise DomainError(f'Розмір "{name}" указано двічі.')
            seen.add(name.casefold())
        if any(not 0 <= percent <= 100 for _, percent in sizes):
            raise DomainError("Частка розміру має бути від 0 до 100 %.")

        removed = [s for s in self._sizes if s.name.casefold() not in seen]
        for size in removed:
            if size.cut_stock != 0 or size.sewn_stock != 0:
                raise DomainError(f'Розмір "{size.name}" не можна прибрати: на ньому є залишок.')
            if any(line.size_id == size.id for line in self._sewn_bom):
                raise DomainError(f'Розмір "{size.name}" використовується в специфікації пошиву.')
            self._sizes.remove(size)

        for i, (name, (_, percent)) in enumerate(zip(names, sizes, strict=True)):
            existing = next((s for s in self._sizes if s.name.casefold() == name.casefold()), None)
            if existing is None:
                self._sizes.append(ProductionSize(name, percent, i + 1))
            else:
                existing.percent = percent
                existing.sort_order = i + 1
        return [s.id for s in removed if s.id is not None]

    def set_links(self, product_ids: Iterable[int], categories: Sequence[tuple[int, str | None, bool]]) -> None:
        """Replaces the links to OpenCart products: direct and through categories with a name filter."""
        wanted = set(product_ids)
        if any(i <= 0 for i in wanted) or any(c <= 0 for c, _, _ in categories):
            raise DomainError("Id товарів і категорій мають бути додатними.")
        category_ids = [c for c, _, _ in categories]
        for category_id in category_ids:
            if category_ids.count(category_id) > 1:
                raise DomainError(f"Категорію {category_id} указано двічі.")

        self._product_links[:] = [link for link in self._product_links if link.product_id in wanted]
        for product_id in sorted(wanted - {link.product_id for link in self._product_links}):
            self._product_links.append(ProductionProductLink(product_id))

        self._category_links[:] = [link for link in self._category_links if link.category_id in category_ids]
        for category_id, name_filter, exact in categories:
            text = (name_filter or "").strip()
            link = next((link for link in self._category_links if link.category_id == category_id), None)
            if link is None:
                self._category_links.append(ProductionCategoryLink(category_id, text, exact))
            else:
                link.name_filter, link.match_exact = text, exact

    def set_size_options(self, mappings: Iterable[tuple[int, int | None]]) -> None:
        """Links the model's sizes to the values of the "Size" option in OpenCart products."""
        for size_id, option_value_id in mappings:
            self.require_size(size_id).option_value_id = option_value_id

    def storefront_quantities(self) -> dict[int, int]:
        """
        How many pieces to show in the store for each linked "Size" option value: the size's sewn stock rounded
        down (OpenCart quantities are whole). If two sizes share a value, the first counts, as in the old module.
        """
        result: dict[int, int] = {}
        linked = sorted((s for s in self._sizes if s.option_value_id is not None), key=lambda s: (s.sort_order, s.id))
        for size in linked:
            result.setdefault(size.option_value_id, int(max(size.sewn_stock, Decimal(0))))
        return result

    def set_sewn_bom(
        self, rows: Sequence[tuple[int | None, int | None, int | None, Decimal]], component_ids_of_model: Iterable[int]
    ) -> None:
        """Replaces the sewing BOM. Each row is either a cut part of this model or a consumable, not both."""
        components = set(component_ids_of_model)
        for size_id, component_id, item_id, qty in rows:
            if (component_id is None) == (item_id is None):
                raise DomainError("Рядок специфікації пошиву: вкажіть або деталь крою, або розхідник.")
            if qty <= 0:
                raise DomainError("Кількість на один виріб має бути більшою за нуль.")
            if size_id is not None:
                self.require_size(size_id)
            if component_id is not None and component_id not in components:
                raise DomainError(f"Деталь {component_id} не належить цій моделі.")
        keys = [(size_id, component_id, item_id) for size_id, component_id, item_id, _ in rows]
        if len(set(keys)) != len(keys):
            raise DomainError("Специфікація пошиву містить повторювані рядки.")
        self._sewn_bom.clear()
        self._sewn_bom.extend(SewnBomLine(*row) for row in rows)

    def remove_component_from_sewn_bom(self, component_id: int) -> None:
        self._sewn_bom[:] = [line for line in self._sewn_bom if line.component_id != component_id]

    @property
    def total_sewn(self) -> Decimal:
        return sum((s.sewn_stock for s in self._sizes), Decimal(0))

    # ── Stock operations ──

    def record_cut(self, size_id: int, quantity: Decimal, comment, user_id, username, now) -> ProductionLogEntry:
        """ "+Покроєно": quantity sets of the size were cut."""
        return self._change(self.require_size(size_id), CUT, _positive(quantity), comment, user_id, username, now)

    def send_to_sewing(self, size_id: int, quantity: Decimal, comment, user_id, username, now) -> ProductionLogEntry:
        """ "В пошив": cut sets went to the seamstresses. Not more than was cut."""
        size = self.require_size(size_id)
        _require_available(size, size.cut_stock, quantity, "розкроєно")
        return self._change(size, CUT, -quantity, comment, user_id, username, now)

    def record_sewn(self, size_id: int, quantity: Decimal, comment, user_id, username, now) -> ProductionLogEntry:
        """ "+Пошито": ready pieces of the size came to stock (for ready_made, a delivery from the supplier)."""
        return self._change(self.require_size(size_id), SEWN, _positive(quantity), comment, user_id, username, now)

    def ship(self, size_id: int, quantity: Decimal, comment, user_id, username, now) -> ProductionLogEntry:
        """ "Відвантажено": sewn pieces left not through an order. Not more than there is."""
        size = self.require_size(size_id)
        _require_available(size, size.sewn_stock, quantity, "пошито")
        return self._change(size, SEWN, -quantity, comment, user_id, username, now)

    def recount(
        self, size_id: int, stage: str, actual: Decimal, comment, user_id, username, now
    ) -> ProductionLogEntry | None:
        """Stocktaking: the cut or sewn stock of the size becomes the counted one; None if it matched."""
        if stage not in (CUT, SEWN):
            raise DomainError(f"Етап має бути {CUT} або {SEWN}.")
        size = self.require_size(size_id)
        require_count(size, actual)
        delta = actual - (size.cut_stock if stage == CUT else size.sewn_stock)
        return None if delta == 0 else self._change(size, stage, delta, comment, user_id, username, now)

    def take_sewn_for_order(
        self, size_id: int, wanted: Decimal, order_id: int, user_id: int, username: str, now: datetime
    ) -> ProductionLogEntry | None:
        """An order takes sewn pieces. Like consumables, it takes what there is: an empty stock never blocks it."""
        size = self.require_size(size_id)
        amount = min(_positive(wanted), max(Decimal(0), size.sewn_stock))
        if amount <= 0:
            return None
        return self._change(size, SEWN, -amount, order_comment(order_id), user_id, username, now)

    def return_sewn_from_order(
        self, size_id: int, quantity: Decimal, order_id: int, user_id: int, username: str, now: datetime
    ) -> ProductionLogEntry:
        """The order left the write-off statuses: the pieces come back to stock."""
        size = self.require_size(size_id)
        return self._change(size, SEWN, _positive(quantity), cancel_comment(order_id), user_id, username, now)

    def require_size(self, size_id: int) -> ProductionSize:
        size = next((s for s in self._sizes if s.id == size_id), None)
        if size is None:
            raise DomainError(f'Розмір {size_id} не належить моделі "{self.name}".')
        return size

    def _change(
        self, size: ProductionSize, stage: str, delta: Decimal, comment: str | None, user_id: int, username: str,
        now: datetime,
    ) -> ProductionLogEntry:  # fmt: skip
        before = size.cut_stock if stage == CUT else size.sewn_stock
        after = before + delta
        text = (comment or "").strip()
        if len(text) > 255:
            raise DomainError("Коментар: не довше 255 символів.")
        if stage == CUT:
            size.cut_stock = after
        else:
            size.sewn_stock = after
        return ProductionLogEntry(self, size, stage, delta, before, after, text, user_id, username, now)


def require_count(size: ProductionSize, actual: Decimal) -> None:
    if actual < 0 or not has_scale(actual):
        raise DomainError(f"Розмір {size.name}: кількість має бути від 0, не більше 3 знаків після коми.")


def _positive(quantity: Decimal) -> Decimal:
    if quantity <= 0:
        raise DomainError("Кількість має бути більшою за нуль.")
    return quantity


def _require_available(size: ProductionSize, available: Decimal, quantity: Decimal, what: str) -> None:
    _positive(quantity)
    if quantity > available:
        raise DomainError(f"Розмір {size.name}: {what} лише {available}, а вказано {quantity}.")


class ProductionComponent:
    """A cut part of a model (dd_stock_production_components) with its own cutting BOM."""

    def __init__(self, production_id: int, name: str | None, sort_order: int) -> None:
        self.id: int | None = None
        self.production_id = production_id
        self._bom: list[ComponentBomLine] = []
        self.rename(name, sort_order)

    @property
    def bom(self) -> list[ComponentBomLine]:
        return list(self._bom)

    def rename(self, name: str | None, sort_order: int) -> None:
        self.name = require_text(name, "Назва деталі", 100)
        self.sort_order = sort_order

    def set_bom(self, rows: Sequence[tuple[int | None, int, Decimal]], size_ids_of_model: Iterable[int]) -> None:
        """Replaces the cutting BOM: how much of a consumable one part takes (per size if needed)."""
        sizes = set(size_ids_of_model)
        for size_id, _, qty in rows:
            if qty <= 0:
                raise DomainError("Кількість розхідника на одну деталь має бути більшою за нуль.")
            if size_id is not None and size_id not in sizes:
                raise DomainError(f"Розмір {size_id} не належить моделі цієї деталі.")
        keys = [(size_id, item_id) for size_id, item_id, _ in rows]
        if len(set(keys)) != len(keys):
            raise DomainError("Специфікація крою містить повторювані рядки.")
        self._bom.clear()
        self._bom.extend(ComponentBomLine(*row) for row in rows)


class ComponentStock:
    """Stock of cut parts of one size (dd_stock_production_component_stock)."""

    def __init__(self, component_id: int, size_id: int) -> None:
        self.component_id = component_id
        self.size_id = size_id
        self.stock = Decimal(0)

    def add(self, quantity: Decimal) -> None:
        self.stock += quantity

    def take_up_to(self, wanted: Decimal) -> Decimal:
        """Parts went to sewing. As with consumables, if there are too few, what there is is taken."""
        taken = min(wanted, max(Decimal(0), self.stock))
        self.stock -= taken
        return taken

    def recount(self, actual: Decimal) -> bool:
        """Stocktaking: the stock becomes the counted one. Returns whether it changed."""
        if actual < 0 or not has_scale(actual):
            raise DomainError("Кількість деталей має бути від 0, не більше 3 знаків після коми.")
        changed = actual != self.stock
        self.stock = actual
        return changed


def resolve_bom[T](
    rows: Iterable[T], size_id: int, size_of: Callable[[T], int | None], key_of: Callable[[T], int | None]
) -> list[T]:
    """
    The old module's rule: the effective rows for a size, one per key (consumable or part). A row for this very
    size wins over a general row (without a size).
    """
    effective: dict[int, T] = {}
    for row in rows:
        key = key_of(row)
        if key is None:
            continue
        if size_of(row) == size_id:
            effective[key] = row
        elif size_of(row) is None:
            effective.setdefault(key, row)
    return list(effective.values())


# ── Sewn pieces at warehouses ──


class SewnBalance:
    """Sewn pieces of a size at one warehouse (erp_production_location). Changes only through SewnPlacement."""

    def __init__(self, size_id: int, warehouse_id: int, quantity: Decimal = Decimal(0)) -> None:
        self.id: int | None = None
        self.size_id = size_id
        self.warehouse_id = warehouse_id
        self.quantity = quantity


class SewnTransfer:
    """A move of sewn pieces between warehouses (erp_production_transfer); None is the main warehouse."""

    def __init__(
        self, production_id: int, size_id: int, from_warehouse_id: int | None, to_warehouse_id: int | None,
        quantity: Decimal, comment: str, user_id: int, username: str, created_at: datetime,
    ) -> None:  # fmt: skip
        self.id: int | None = None
        self.production_id = production_id
        self.size_id = size_id
        self.from_warehouse_id = from_warehouse_id
        self.to_warehouse_id = to_warehouse_id
        self.quantity = quantity
        self.comment = comment
        self.user_id = user_id
        self.username = username
        self.created_at = created_at


class SewnPlacement:
    """
    Where a model's sewn pieces lie. As with consumables: a size's sewn stock stays the total (the old site, orders
    and the store use it); warehouses have their own balances, and the main warehouse is the rest.
    """

    def __init__(self, model: ProductionModel, balances: Iterable[SewnBalance]) -> None:
        self.model = model
        self._balances = {(b.size_id, b.warehouse_id): b for b in balances}
        self.created: list[SewnBalance] = []

    def balances(self, size_id: int) -> list[SewnBalance]:
        return [b for b in self._balances.values() if b.size_id == size_id]

    def at(self, size_id: int, warehouse_id: int | None) -> Decimal:
        size = self.model.require_size(size_id)
        if warehouse_id is not None:
            balance = self._balances.get((size_id, warehouse_id))
            return balance.quantity if balance else Decimal(0)
        return size.sewn_stock - sum((b.quantity for b in self.balances(size_id)), Decimal(0))

    def move(
        self, size_id: int, from_id: int | None, to_id: int | None, quantity: Decimal, comment: str | None,
        user_id: int, username: str, now: datetime,
    ) -> SewnTransfer:  # fmt: skip
        if from_id == to_id:
            raise DomainError("Звідки й куди переміщують — має бути різне місце.")
        if quantity <= 0 or not has_scale(quantity):
            raise DomainError("Кількість має бути більшою за нуль, не більше 3 знаків після коми.")
        text = (comment or "").strip()
        if len(text) > 255:
            raise DomainError("Коментар: не довше 255 символів.")
        self._require_available(size_id, from_id, quantity)
        self._change(size_id, from_id, -quantity)
        self._change(size_id, to_id, quantity)
        return SewnTransfer(self.model.id, size_id, from_id, to_id, quantity, text, user_id, username, now)

    def sewn_at(self, size_id: int, warehouse_id: int | None, quantity: Decimal, comment, user_id, username, now):
        """Ready pieces came to a warehouse (or the main one): the sewn stock grows, the log gets a row."""
        entry = self.model.record_sewn(size_id, quantity, comment, user_id, username, now)
        self._change(size_id, warehouse_id, quantity)
        return entry

    def ship_from(self, size_id: int, warehouse_id: int | None, quantity: Decimal, comment, user_id, username, now):
        """Pieces left a place not through an order. Not more than there is."""
        # With nothing of the size at warehouses the main one is the sewn stock: the model's own check applies.
        if quantity > 0 and (warehouse_id is not None or any(b.quantity != 0 for b in self.balances(size_id))):
            self._require_available(size_id, warehouse_id, quantity)
        entry = self.model.ship(size_id, quantity, comment, user_id, username, now)
        self._change(size_id, warehouse_id, -quantity)
        return entry

    def recount_at(self, size_id: int, warehouse_id: int | None, actual: Decimal, comment, user_id, username, now):
        """Stocktaking of the sewn size in one place; the sewn stock changes by the same difference."""
        size = self.model.require_size(size_id)
        require_count(size, actual)
        delta = actual - self.at(size_id, warehouse_id)
        if delta == 0:
            return None
        target = max(size.sewn_stock + delta, Decimal(0))
        entry = self.model.recount(size_id, SEWN, target, comment, user_id, username, now)
        self._change(size_id, warehouse_id, delta)
        return entry

    def _change(self, size_id: int, warehouse_id: int | None, delta: Decimal) -> None:
        if warehouse_id is None:
            return  # the main warehouse is the rest; it changes by itself
        balance = self._balances.get((size_id, warehouse_id))
        if balance is None:
            balance = SewnBalance(size_id, warehouse_id)
            self._balances[(size_id, warehouse_id)] = balance
            self.created.append(balance)
        balance.quantity += delta

    def _require_available(self, size_id: int, warehouse_id: int | None, quantity: Decimal) -> None:
        available = max(self.at(size_id, warehouse_id), Decimal(0))
        if available < quantity:
            place = "на основному складі" if warehouse_id is None else "на цьому складі"
            raise DomainError(
                f"Розмір {self.model.require_size(size_id).name}: {place} пошито {plain(available, 3)}, "
                f"потрібно {plain(quantity, 3)}."
            )


# ── Sewing plan ──


@dataclass(frozen=True)
class SewingDemand:
    """One order's need for a size: how many ordered and how many sewn pieces it already holds."""

    order_id: int
    size_id: int
    ordered: Decimal
    held: Decimal
    deadline: date | None

    @property
    def missing(self) -> Decimal:
        return max(Decimal(0), self.ordered - self.held)

    def with_held(self, held: Decimal) -> "SewingDemand":
        return replace(self, held=held)


@dataclass(frozen=True)
class SewingPlanLine:
    """missing: what active orders lack; to_sew: after the free sewn stock; from_cut: already cut; to_cut: the rest."""

    size_id: int
    ordered: Decimal
    missing: Decimal
    to_sew: Decimal
    from_cut: Decimal
    to_cut: Decimal
    deadline: date | None
    order_ids: list[int] = field(default_factory=list)


def plan_for_size(
    size_id: int, sewn_stock: Decimal, cut_stock: Decimal, demands: Iterable[SewingDemand]
) -> SewingPlanLine:
    """
    The free sewn stock (what orders already took does not count) covers the need first; the rest must be sewn.
    Cut sets are sewn first, so only what the cut stock does not cover must be cut. The deadline is the earliest
    among the orders that lack something.
    """
    rows = [d for d in demands if d.size_id == size_id]
    missing = sum((d.missing for d in rows), Decimal(0))
    to_sew = max(Decimal(0), missing - max(Decimal(0), sewn_stock))
    from_cut = min(to_sew, max(Decimal(0), cut_stock))
    waiting = [d for d in rows if d.missing > 0]
    deadlines = [d.deadline for d in waiting if d.deadline is not None]
    return SewingPlanLine(
        size_id=size_id,
        ordered=sum((d.ordered for d in rows), Decimal(0)),
        missing=missing,
        to_sew=to_sew,
        from_cut=from_cut,
        to_cut=to_sew - from_cut,
        deadline=min(deadlines) if deadlines else None,
        order_ids=sorted({d.order_id for d in waiting}),
    )


def deadline(order_date: datetime, production_days: int) -> date | None:
    """
    The ready date as in the old site's order list: order date + the category's production time.
    0 means "not set" (the old site then showed the order date itself, and everything looked overdue).
    """
    return order_date.date() + timedelta(days=production_days) if production_days > 0 else None
