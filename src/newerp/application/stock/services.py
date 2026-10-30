"""Consumables stock scenarios: items, reorder report, purchase orders, warehouses, stocktaking."""

import math
from collections.abc import Iterable
from decimal import Decimal

from newerp.application.common import Clock, LookupItem, NotFoundError, Spreadsheet, ukrainian_key
from newerp.application.locations import LocationStore
from newerp.application.stock.models import (
    AdjustStockRequest,
    ApplyStocktakingRequest,
    CreatePurchaseOrderRequest,
    CreateStockCategoryRequest,
    CreateStockSupplierRequest,
    ItemPlacementDto,
    PlaceDto,
    PurchaseOrderLineRequest,
    ReceivePurchaseOrderRequest,
    ReorderLineDto,
    ReorderReportDto,
    SaveStockItemRequest,
    SetPurchaseOrderLinesRequest,
    StockByWarehouseDto,
    StockByWarehousePlace,
    StockByWarehouseRow,
    StockItemDto,
    StockMovementDto,
    StockSupplierDto,
    StocktakingLineDto,
    StocktakingPreviewDto,
    StocktakingResult,
    StockTransferDto,
    SupplierReorderDto,
    TransferStockRequest,
)
from newerp.application.stock.ports import (
    PurchaseOrderRepository,
    StockItemFilter,
    StockLocationRepository,
    StockRepository,
)
from newerp.domain.common import DomainError, plain, round_money
from newerp.domain.stock import (
    STOCKTAKING_COLUMNS,
    PurchaseOrder,
    StockBalance,
    StockCategory,
    StockItem,
    StockMovement,
    StockPlacement,
    StockSupplier,
    parse_stocktaking,
    stocktaking_comment,
)

MAIN_NAME = "Основний склад"
NO_SUPPLIER_NAME = "Без постачальника"
MAX_FILE_BYTES = 5 * 1024 * 1024


def movement_dto(m: StockMovement, item_id: int) -> StockMovementDto:
    return StockMovementDto(
        id=m.id,
        item_id=item_id,
        delta=m.delta,
        quantity_before=m.stock_before,
        quantity_after=m.stock_after,
        reason=m.comment,
        user_id=m.user_id,
        username=m.username,
        created_at=m.created_at,
    )


class StockService:
    def __init__(self, repository: StockRepository, locations: StockLocationRepository, clock: Clock) -> None:
        self._repository = repository
        self._locations = locations
        self._clock = clock

    async def create_category(self, request: CreateStockCategoryRequest) -> int:
        category = StockCategory(request.name, request.sort_order)
        if await self._repository.category_name_taken(category.name):
            raise DomainError(f'Категорія "{category.name}" вже існує.')
        self._repository.add(category)
        await self._repository.save_changes()
        return category.id

    async def create_supplier(self, request: CreateStockSupplierRequest) -> int:
        supplier = StockSupplier(request.name, request.phone, request.email, request.categories, request.notes)
        self._repository.add(supplier)
        await self._repository.save_changes()
        return supplier.id

    async def create_item(self, request: SaveStockItemRequest) -> int:
        await self._require_references(request)
        item = StockItem(
            request.category_id, request.supplier_id, request.name, request.unit, request.min_quantity,
            request.used_for, request.price,
        )  # fmt: skip
        self._repository.add(item)
        await self._repository.save_changes()
        return item.id

    async def update_item(self, item_id: int, request: SaveStockItemRequest) -> None:
        """Changes the description (name, category, supplier, price, minimum). Never the quantity."""
        item = await self._require_item(item_id)
        await self._require_references(request)
        item.update(
            request.category_id, request.supplier_id, request.name, request.unit, request.min_quantity,
            request.used_for, request.price,
        )  # fmt: skip
        await self._repository.save_changes()

    async def adjust(self, item_id: int, request: AdjustStockRequest, user_id: int, username: str) -> StockMovementDto:
        item = await self._require_item(item_id)
        # Without a warehouse it is the main one: what lies at other warehouses is not used from here.
        placement = StockPlacement(item, await self._locations.find_balances(item_id))
        movement = placement.adjust_at(None, request.delta, request.reason, user_id, username, self._clock.now())
        self._repository.add(movement)
        await self._repository.save_changes()
        return movement_dto(movement, item.id)

    async def set_min_quantity(self, item_id: int, min_quantity: Decimal) -> None:
        item = await self._require_item(item_id)
        item.set_min_quantity(min_quantity)
        await self._repository.save_changes()

    async def _require_item(self, item_id: int) -> StockItem:
        item = await self._repository.find_item(item_id)
        if item is None:
            raise NotFoundError(f"Позицію складу {item_id} не знайдено.")
        return item

    async def _require_references(self, request: SaveStockItemRequest) -> None:
        if request.category_id is not None and not await self._repository.category_exists(request.category_id):
            raise DomainError(f"Категорії {request.category_id} не існує.")
        if request.supplier_id is not None and not await self._repository.supplier_exists(request.supplier_id):
            raise DomainError(f"Постачальника {request.supplier_id} не існує.")


class ReorderService:
    """The "what to reorder" report: items below the minimum, grouped by supplier."""

    def __init__(
        self, repository: StockRepository, purchase_orders: PurchaseOrderRepository, target_factor: Decimal
    ) -> None:
        self._repository = repository
        self._purchase_orders = purchase_orders
        # Up to how many minimums to reorder: 2 means "bring the quantity up to 2 × minimum", a cushion so
        # the item is not below the minimum right after the delivery.
        self._target_factor = target_factor

    async def get_report(self) -> ReorderReportDto:
        """What is already ordered and not received is subtracted, so a repeated report does not offer it again."""
        items = await self._repository.list_items(StockItemFilter(below_minimum_only=True))
        suppliers = {s.id: s for s in await self._repository.list_suppliers()}
        return build_reorder(items, suppliers, await self._purchase_orders.on_order(), self._target_factor)


def build_reorder(
    items: Iterable[StockItemDto],
    suppliers: dict[int, StockSupplierDto],
    on_order: dict[int, Decimal],
    target_factor: Decimal,
) -> ReorderReportDto:
    """Suggested = factor × minimum − quantity − already ordered, rounded up. Items needing nothing are left out."""
    groups: dict[int | None, list[ReorderLineDto]] = {}
    for item in items:
        if not item.is_below_minimum:
            continue
        ordered = on_order.get(item.id, Decimal(0))
        suggested = Decimal(math.ceil(max(Decimal(0), target_factor * item.min_quantity - item.quantity - ordered)))
        if suggested <= 0:
            continue
        groups.setdefault(item.supplier_id, []).append(
            ReorderLineDto(
                item_id=item.id,
                name=item.name,
                unit=item.unit,
                quantity=item.quantity,
                min_quantity=item.min_quantity,
                on_order=ordered,
                suggested=suggested,
                price=item.price,
                sum=suggested * item.price,
            )  # fmt: skip
        )

    result = []
    for supplier_id, lines in groups.items():
        supplier = suppliers.get(supplier_id) if supplier_id is not None else None
        lines.sort(key=lambda line: ukrainian_key(line.name))
        result.append(
            SupplierReorderDto(
                supplier_id=supplier_id,
                supplier_name=supplier.name if supplier else NO_SUPPLIER_NAME,
                phone=supplier.phone if supplier else "",
                email=supplier.email if supplier else "",
                lines=lines,
                total=sum((line.sum for line in lines), Decimal(0)),
                message_text=message_text(lines),
            )
        )
    # Suppliers alphabetically, items without a supplier last.
    result.sort(key=lambda s: (s.supplier_id is None, ukrainian_key(s.supplier_name)))
    return ReorderReportDto(suppliers=result, total=sum((s.total for s in result), Decimal(0)))


def message_text(lines: Iterable[ReorderLineDto]) -> str:
    """A text to copy straight to the supplier."""
    text = "Добрий день! Хочемо замовити:"
    for line in lines:
        text += f"\n— {line.name}: {plain(line.suggested, 3)} {line.unit}"
    return text + "\nДякуємо!"


class PurchaseOrderService:
    """Purchase orders: created by hand or from the reorder report, sent, received."""

    def __init__(
        self,
        orders: PurchaseOrderRepository,
        stock: StockRepository,
        reorder: ReorderService,
        locations: StockLocationRepository,
        warehouses: LocationStore,
        clock: Clock,
    ) -> None:
        self._orders = orders
        self._stock = stock
        self._reorder = reorder
        self._locations = locations
        self._warehouses = warehouses
        self._clock = clock

    async def create_from_reorder(self, supplier_id: int, user_id: int) -> int:
        """A draft with what the reorder report offers (quantity and price of the item)."""
        report = await self._reorder.get_report()
        group = next((s for s in report.suppliers if s.supplier_id == supplier_id), None)
        if group is None:
            raise DomainError(f"У постачальника {supplier_id} зараз нічого дозамовляти.")
        lines = [(line.item_id, line.suggested, line.price) for line in group.lines]
        order = PurchaseOrder(supplier_id, lines, "Зі звіту «що дозамовити»", user_id, self._clock.now())
        self._orders.add(order)
        await self._orders.save_changes()
        return order.id

    async def create(self, request: CreatePurchaseOrderRequest, user_id: int) -> int:
        if not await self._stock.supplier_exists(request.supplier_id):
            raise DomainError(f"Постачальника {request.supplier_id} не існує.")
        lines = await self._valid_lines(request.lines)
        order = PurchaseOrder(request.supplier_id, lines, request.comment, user_id, self._clock.now())
        self._orders.add(order)
        await self._orders.save_changes()
        return order.id

    async def set_lines(self, order_id: int, request: SetPurchaseOrderLinesRequest) -> None:
        order = await self._require(order_id)
        order.set_lines(await self._valid_lines(request.lines))
        await self._orders.save_changes()

    async def mark_ordered(self, order_id: int) -> None:
        (await self._require(order_id)).mark_ordered(self._clock.now())
        await self._orders.save_changes()

    async def cancel(self, order_id: int) -> None:
        (await self._require(order_id)).cancel(self._clock.now())
        await self._orders.save_changes()

    async def receive(self, order_id: int, request: ReceivePurchaseOrderRequest, user_id: int, username: str) -> None:
        """Quantities grow, receipts go into the stock log, in one save. With a warehouse the delivery lies there."""
        order = await self._require(order_id)
        if request.warehouse_id is not None and await self._warehouses.get(request.warehouse_id) is None:
            raise NotFoundError(f"Склад {request.warehouse_id} не знайдено.")

        items = {i.id: i for i in await self._orders.find_items([line.item_id for line in order.lines])}
        receipts = [(line.line_id, line.quantity) for line in request.lines or []]
        movements = order.receive(receipts, items, user_id, username, self._clock.now())
        for movement in movements:
            self._orders.add(movement)

        if request.warehouse_id is not None:
            # One item may come in several lines: each balance changes once.
            received: dict[int, Decimal] = {}
            for movement in movements:
                received[movement.item.id] = received.get(movement.item.id, Decimal(0)) + movement.delta
            for item_id, quantity in received.items():
                placement = StockPlacement(items[item_id], await self._locations.find_balances(item_id))
                placement.received_at(request.warehouse_id, quantity)
                for balance in placement.created:
                    self._locations.add(balance)
        await self._orders.save_changes()

    async def _valid_lines(self, lines: list[PurchaseOrderLineRequest] | None) -> list[tuple[int, Decimal, Decimal]]:
        result = [(line.item_id, line.quantity, line.price) for line in lines or []]
        ids = list(dict.fromkeys(item_id for item_id, _, _ in result))
        found = {item.id for item in await self._orders.find_items(ids)}
        missing = [item_id for item_id in ids if item_id not in found]
        if missing:
            raise DomainError(f"Розхідників {', '.join(map(str, missing))} немає на складі.")
        return result

    async def _require(self, order_id: int) -> PurchaseOrder:
        order = await self._orders.find(order_id)
        if order is None:
            raise NotFoundError(f"Замовлення постачальнику №{order_id} не знайдено.")
        return order


class StockLocationService:
    """
    Consumables at several warehouses (the "Склади й пункти видачі" directory). The total stays in dd_stock_items,
    so the old site and the other operations work as before; what lies at no warehouse is the main warehouse.
    """

    def __init__(
        self, stock: StockRepository, locations: StockLocationRepository, warehouses: LocationStore, clock: Clock
    ) -> None:
        self._stock = stock
        self._locations = locations
        self._warehouses = warehouses
        self._clock = clock

    async def warehouses(self) -> list[LookupItem]:
        """Warehouses to put to or take from, for choice lists."""
        rows = [LookupItem(id=w.id, name=w.name) for w in await self._warehouses.list_all(None)]
        return sorted(rows, key=lambda w: ukrainian_key(w.name))

    async def get(self, item_id: int) -> ItemPlacementDto:
        item = await self._stock.get_item_dto(item_id)
        if item is None:
            raise _item_not_found(item_id)
        balances = await self._locations.find_balances(item_id)
        main = item.quantity - sum((b.quantity for b in balances), Decimal(0))
        return _placement_dto(item.id, item.name, item.unit, item.quantity, main, balances, await self._names())

    async def transfer(
        self, item_id: int, request: TransferStockRequest, user_id: int, username: str
    ) -> ItemPlacementDto:
        """Moves between two places; the total stays, the move goes into the transfer log."""
        names = await self._names()
        require_warehouse(request.from_warehouse_id, names)
        require_warehouse(request.to_warehouse_id, names)
        placement = await self._placement(item_id)
        transfer = placement.move(
            request.from_warehouse_id, request.to_warehouse_id, request.quantity, request.comment, user_id, username,
            self._clock.now(),
        )  # fmt: skip
        self._add_created(placement)
        self._locations.add(transfer)
        await self._stock.save_changes()
        item = placement.item
        return _placement_dto(item.id, item.name, item.unit, item.quantity, placement.main, placement.balances, names)

    async def adjust(
        self, item_id: int, warehouse_id: int, request: AdjustStockRequest, user_id: int, username: str
    ) -> StockMovementDto:
        """Receipt (+) or use (−) at a warehouse; the log comment gets the warehouse name."""
        names = await self._names()
        require_warehouse(warehouse_id, names)
        placement = await self._placement(item_id)
        # An empty reason is passed as is, so the domain refuses with its usual message.
        reason = request.reason
        if reason and reason.strip():
            reason = f"{reason.strip()} · {names[warehouse_id]}"[:255]
        movement = placement.adjust_at(warehouse_id, request.delta, reason, user_id, username, self._clock.now())
        self._add_created(placement)
        self._stock.add(movement)
        await self._stock.save_changes()
        return movement_dto(movement, item_id)

    async def transfers(self, item_id: int) -> list[StockTransferDto]:
        names = await self._names()
        return [
            StockTransferDto(
                id=t.id,
                from_warehouse_id=t.from_warehouse_id,
                from_=place_name(t.from_warehouse_id, names),
                to_warehouse_id=t.to_warehouse_id,
                to=place_name(t.to_warehouse_id, names),
                quantity=t.quantity,
                comment=t.comment,
                username=t.username,
                created_at=t.created_at,
            )
            for t in await self._locations.list_transfers(item_id)
        ]

    async def by_warehouse(self) -> StockByWarehouseDto:
        """Every active item with its quantity in every place; only warehouses holding something get a column."""
        names = await self._names()
        items = await self._stock.list_items(StockItemFilter())
        balances: dict[int, dict[int, Decimal]] = {}
        for row in await self._locations.list_balances():
            balances.setdefault(row.item_id, {})[row.warehouse_id] = row.quantity
        columns = sorted(
            {warehouse_id for at in balances.values() for warehouse_id in at},
            key=lambda warehouse_id: ukrainian_key(place_name(warehouse_id, names)),
        )

        values = [Decimal(0)] * (len(columns) + 1)  # 0 is the main warehouse
        rows = []
        for item in items:
            at = balances.get(item.id, {})
            quantities = [item.quantity - sum(at.values(), Decimal(0))]
            quantities += [at.get(warehouse_id, Decimal(0)) for warehouse_id in columns]
            for i, quantity in enumerate(quantities):
                values[i] += max(quantity, Decimal(0)) * item.price
            rows.append(
                StockByWarehouseRow(
                    item_id=item.id,
                    name=item.name,
                    unit=item.unit,
                    category_name=item.category_name,
                    total=item.quantity,
                    quantities=quantities,
                )  # fmt: skip
            )

        places = [StockByWarehousePlace(warehouse_id=None, name=MAIN_NAME, value=round_money(values[0]))]
        places += [
            StockByWarehousePlace(warehouse_id=w, name=place_name(w, names), value=round_money(values[i + 1]))
            for i, w in enumerate(columns)
        ]
        return StockByWarehouseDto(places=places, rows=rows)

    async def _placement(self, item_id: int) -> StockPlacement:
        item = await self._stock.find_item(item_id)
        if item is None or not item.is_active:
            raise _item_not_found(item_id)
        return StockPlacement(item, await self._locations.find_balances(item_id))

    def _add_created(self, placement: StockPlacement) -> None:
        for balance in placement.created:
            self._locations.add(balance)

    async def _names(self) -> dict[int, str]:
        return {w.id: w.name for w in await self._warehouses.list_all(None)}


def _placement_dto(
    item_id: int,
    name: str,
    unit: str,
    total: Decimal,
    main: Decimal,
    balances: Iterable[StockBalance],
    names: dict[int, str],
) -> ItemPlacementDto:
    places = [PlaceDto(warehouse_id=None, name=MAIN_NAME, quantity=main)]
    places += [
        PlaceDto(warehouse_id=b.warehouse_id, name=place_name(b.warehouse_id, names), quantity=b.quantity)
        for b in sorted(
            (b for b in balances if b.quantity != 0), key=lambda b: ukrainian_key(place_name(b.warehouse_id, names))
        )
    ]
    return ItemPlacementDto(item_id=item_id, name=name, unit=unit, total=total, places=places)


def require_warehouse(warehouse_id: int | None, names: dict[int, str]) -> None:
    if warehouse_id is not None and warehouse_id not in names:
        raise NotFoundError(f"Склад {warehouse_id} не знайдено.")


def place_name(warehouse_id: int | None, names: dict[int, str]) -> str:
    if warehouse_id is None:
        return MAIN_NAME
    return names.get(warehouse_id, f"Склад {warehouse_id}")


def _item_not_found(item_id: int) -> NotFoundError:
    return NotFoundError(f"Позицію складу {item_id} не знайдено.")


class StocktakingService:
    """Stocktaking: a sheet to count, a preview of the uploaded one, and applying it with log rows."""

    def __init__(
        self,
        repository: StockRepository,
        locations: StockLocationRepository,
        warehouses: LocationStore,
        spreadsheet: Spreadsheet,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._locations = locations
        self._warehouses = warehouses
        self._spreadsheet = spreadsheet
        self._clock = clock

    async def booked(self, warehouse_id: int | None) -> dict[int, Decimal]:
        """What lies in the place by the books for every active item: a warehouse's balance, or the rest."""
        await self._require_warehouse(warehouse_id)
        located: dict[int, Decimal] = {}
        for b in await self._locations.list_balances():
            if warehouse_id is None or b.warehouse_id == warehouse_id:
                located[b.item_id] = located.get(b.item_id, Decimal(0)) + b.quantity
        return {
            i.id: (i.quantity - located.get(i.id, Decimal(0)))
            if warehouse_id is None
            else located.get(i.id, Decimal(0))
            for i in await self._repository.list_items(StockItemFilter())
        }

    async def sheet(self, category_id: int | None, warehouse_id: int | None) -> bytes:
        """Active items (of one category if given) with the booked quantity and an empty "Фактично" column."""
        booked = await self.booked(warehouse_id)
        rows = [
            [i.id, i.name, i.category_name or "", i.unit, booked[i.id]]
            for i in await self._repository.list_items(StockItemFilter(category_id=category_id))
        ]
        return self._spreadsheet.write("Інвентаризація", STOCKTAKING_COLUMNS, rows)

    async def preview(self, content: bytes, warehouse_id: int | None) -> StocktakingPreviewDto:
        """Reads a sheet counted in the place; writes nothing."""
        counts, errors = parse_stocktaking(self._spreadsheet.read(content))
        items = {i.id: i for i in await self._repository.list_items(StockItemFilter())}
        booked = await self.booked(warehouse_id)
        lines = []
        for count in counts:
            item = items.get(count.item_id)
            if item is None:
                errors.append(f"Позиції {count.item_id} немає на складі.")
                continue
            before = booked[item.id]
            lines.append(
                StocktakingLineDto(
                    item_id=item.id,
                    name=item.name,
                    unit=item.unit,
                    booked=before,
                    actual=count.actual,
                    difference=count.actual - before,
                )  # fmt: skip
            )
        lines.sort(key=lambda line: ukrainian_key(line.name))
        return StocktakingPreviewDto(lines=lines, errors=errors)

    async def apply(self, request: ApplyStocktakingRequest, user_id: int, username: str) -> StocktakingResult:
        """
        Sets every counted item in the place to the counted quantity in one save; the total changes by the same
        difference, the log says "Інвентаризація". If any count is wrong, nothing is written.
        """
        counts = request.counts or []
        if not counts:
            raise DomainError("Немає жодної порахованої позиції.")
        seen: set[int] = set()
        for count in counts:
            if count.item_id in seen:
                raise DomainError(f"Позицію {count.item_id} указано двічі.")
            seen.add(count.item_id)
        await self._require_warehouse(request.warehouse_id)

        comment = stocktaking_comment(request.comment)
        now = self._clock.now()
        changed = 0
        value = Decimal(0)
        for count in sorted(counts, key=lambda c: c.item_id):
            item = await self._repository.find_item(count.item_id)
            if item is None or not item.is_active:
                raise DomainError(f"Позиції {count.item_id} немає на складі.")
            placement = StockPlacement(item, await self._locations.find_balances(item.id))
            try:
                movement = placement.recount_at(request.warehouse_id, count.actual, comment, user_id, username, now)
            except DomainError as error:
                raise DomainError(f"{item.name}: {error}") from error
            for balance in placement.created:
                self._locations.add(balance)
            if movement is not None:
                self._repository.add(movement)
                changed += 1
                value += movement.delta * item.price
        await self._repository.save_changes()
        return StocktakingResult(counted=len(counts), changed=changed, value=round_money(value))

    async def _require_warehouse(self, warehouse_id: int | None) -> None:
        if warehouse_id is not None and await self._warehouses.get(warehouse_id) is None:
            raise NotFoundError(f"Склад {warehouse_id} не знайдено.")
