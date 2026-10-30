"""Production scenarios (based on the old site's StockTrack module)."""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from newerp.application.common import Clock, NotFoundError, ukrainian_key
from newerp.application.locations import LocationStore
from newerp.application.production.models import (
    ProductionOperation,
    ProductionOperationRequest,
    ProductionOperationResult,
    ProductionStocktakingRequest,
    ProductionStocktakingResult,
    SaveComponentRequest,
    SaveProductionModelRequest,
    SetComponentBomRequest,
    SetProductionLinksRequest,
    SetSewnBomRequest,
    SewingPlanDto,
    SewingPlanIssueDto,
    SewingPlanRowDto,
    SewnByWarehouseDto,
    SewnByWarehousePlace,
    SewnByWarehouseRow,
    SewnTransferDto,
    SizeChangeDto,
    SizePlacesDto,
    StorefrontSyncResult,
    TransferSewnRequest,
)
from newerp.application.production.ports import (
    ProductionRepository,
    SewingPlanSource,
    SewnLocationRepository,
    StorefrontStock,
)
from newerp.application.stock.models import PlaceDto
from newerp.application.stock.services import MAIN_NAME, place_name, require_warehouse
from newerp.domain.common import DomainError, plain
from newerp.domain.orders import OrderStatusChange
from newerp.domain.production import (
    CUT,
    ComponentStock,
    ProductionComponent,
    ProductionModel,
    ProductionSize,
    SewingDemand,
    SewnBalance,
    SewnPlacement,
    deadline,
    plan_for_size,
    resolve_bom,
)
from newerp.domain.stock import StockItem, stocktaking_comment

SYSTEM_USERNAME = "system"  # as on the old site: automatic log rows are signed "system"


class ProductionService:
    def __init__(
        self,
        repository: ProductionRepository,
        storefront: StorefrontStock,
        locations: SewnLocationRepository,
        warehouses: LocationStore,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._storefront = storefront
        self._locations = locations
        self._warehouses = warehouses
        self._clock = clock

    async def record(
        self,
        production_id: int,
        operation: ProductionOperation,
        request: ProductionOperationRequest,
        user_id: int,
        username: str,
    ) -> ProductionOperationResult:
        """
        One operation for several sizes at once. Cutting and sewing write off consumables and parts by the BOMs: if
        something is short, what there is gets written off and the shortage goes to warnings. One save for all.
        """
        model = await self._require_model(production_id)
        rows = request.quantities or []
        if not rows:
            raise DomainError("Вкажіть кількість хоча б для одного розміру.")
        _require_unique([r.size_id for r in rows], "Розмір {} указано двічі.")
        sewn_ops = (ProductionOperation.SEWN, ProductionOperation.SHIP)
        if request.warehouse_id is not None:
            if operation not in sewn_ops:
                raise DomainError("Склад вказують лише для пошитого й відвантаженого: крій живе на основному складі.")
            if await self._warehouses.get(request.warehouse_id) is None:
                raise NotFoundError(f"Склад {request.warehouse_id} не знайдено.")

        # Sewn pieces have places: shipping from the main warehouse does not take what lies at other ones.
        placement = (
            SewnPlacement(model, await self._locations.find_balances(production_id)) if operation in sewn_ops else None
        )
        now = self._clock.now()
        consumption = (
            await self._load_consumption(model)
            if operation in (ProductionOperation.CUT, ProductionOperation.SEWN)
            else None
        )

        changes = []
        comment = request.comment
        for row in rows:
            size_id, quantity = row.size_id, row.quantity
            match operation:
                case ProductionOperation.CUT:
                    entry = model.record_cut(size_id, quantity, comment, user_id, username, now)
                case ProductionOperation.SEND_TO_SEWING:
                    entry = model.send_to_sewing(size_id, quantity, comment, user_id, username, now)
                case ProductionOperation.SEWN:
                    entry = placement.sewn_at(size_id, request.warehouse_id, quantity, comment, user_id, username, now)
                case ProductionOperation.SHIP:
                    entry = placement.ship_from(
                        size_id, request.warehouse_id, quantity, comment, user_id, username, now
                    )
            self._repository.add(entry)
            changes.append(
                SizeChangeDto(
                    size_id=size_id, size_name=entry.size_name, before=entry.stock_before, after=entry.stock_after
                )
            )
            if operation == ProductionOperation.CUT:
                consumption.for_cut(entry.size_name, size_id, quantity, user_id, username, now)
            elif operation == ProductionOperation.SEWN:
                consumption.for_sewing(entry.size_name, size_id, quantity, user_id, username, now)

        for balance in placement.created if placement else []:
            self._locations.add(balance)
        await self._repository.save_changes()
        # As in the old module: after a change of the sewn stock it goes to the store at once.
        if operation in sewn_ops:
            await self._storefront.push([production_id])
        return ProductionOperationResult(sizes=changes, warnings=consumption.warnings if consumption else [])

    async def recount(
        self, production_id: int, request: ProductionStocktakingRequest, user_id: int, username: str
    ) -> ProductionStocktakingResult:
        """
        Stocktaking of a model: cut and sewn stock of every counted size and every counted parts stock become the
        counted ones. Size differences go to the log with "Інвентаризація". One save: a wrong count writes nothing.
        """
        model = await self._require_model(production_id)
        sizes = request.sizes or []
        components = request.components or []
        if not sizes and not components:
            raise DomainError("Немає жодної порахованої кількості.")
        _require_unique([r.size_id for r in sizes], "Розмір {} указано двічі.")
        seen: set[tuple[int, int]] = set()
        for row in components:
            if (row.component_id, row.size_id) in seen:
                raise DomainError(f"Деталь {row.component_id} розміру {row.size_id} указано двічі.")
            seen.add((row.component_id, row.size_id))
        if request.warehouse_id is not None:
            if components or any(r.cut is not None for r in sizes):
                raise DomainError("На складі рахують лише пошите: крій і деталі живуть на основному складі.")
            if await self._warehouses.get(request.warehouse_id) is None:
                raise NotFoundError(f"Склад {request.warehouse_id} не знайдено.")

        comment = stocktaking_comment(request.comment)
        now = self._clock.now()
        cut, sewn = [], []
        # Sewn pieces are counted in one place (the main warehouse without a warehouse); other places stay.
        placement = SewnPlacement(model, await self._locations.find_balances(production_id))
        for row in sizes:
            if row.cut is not None:
                entry = model.recount(row.size_id, CUT, row.cut, comment, user_id, username, now)
                if entry is not None:
                    self._repository.add(entry)
                    cut.append(_change_dto(row.size_id, entry))
            if row.sewn is not None:
                entry = placement.recount_at(
                    row.size_id, request.warehouse_id, row.sewn, comment, user_id, username, now
                )
                if entry is not None:
                    self._repository.add(entry)
                    sewn.append(_change_dto(row.size_id, entry))
        for balance in placement.created:
            self._locations.add(balance)

        changed_components = 0
        if components:
            own = await self._repository.component_ids(production_id)
            stocks = {(s.component_id, s.size_id): s for s in await self._repository.find_component_stocks(own)}
            for row in components:
                if row.component_id not in own:
                    raise DomainError(f'Деталь {row.component_id} не належить моделі "{model.name}".')
                model.require_size(row.size_id)
                stock = stocks.get((row.component_id, row.size_id))
                if stock is None:
                    stock = ComponentStock(row.component_id, row.size_id)
                    if not stock.recount(row.actual):
                        continue  # counted 0 where there was no stock
                    self._repository.add(stock)
                    changed_components += 1
                elif stock.recount(row.actual):
                    changed_components += 1

        await self._repository.save_changes()
        if sewn:
            await self._storefront.push([production_id])
        return ProductionStocktakingResult(cut=cut, sewn=sewn, components=changed_components)

    async def push_storefront(self) -> StorefrontSyncResult:
        """Push the sewn stock of all active models to the store (for example, after changes on the old site)."""
        return await self._storefront.push(None)

    async def create_model(self, request: SaveProductionModelRequest) -> int:
        model = ProductionModel(request.name, request.type, request.min_total, request.sort_order)
        model.set_sizes(_sizes(request))
        self._repository.add(model)
        await self._repository.save_changes()
        return model.id

    async def update_model(self, production_id: int, request: SaveProductionModelRequest) -> None:
        model = await self._require_model(production_id)
        model.update(request.name, request.type, request.min_total, request.sort_order)
        removed = model.set_sizes(_sizes(request))
        if removed and await self._repository.sizes_in_use(removed):
            raise DomainError("Прибраний розмір використовується в специфікації крою або має розкроєні деталі.")
        await self._repository.save_changes()

    async def deactivate_model(self, production_id: int) -> None:
        """As in the old module: a model is not deleted but marked inactive."""
        (await self._require_model(production_id)).deactivate()
        await self._repository.save_changes()

    async def set_links(self, production_id: int, request: SetProductionLinksRequest) -> None:
        model = await self._require_model(production_id)
        model.set_links(
            request.product_ids or [],
            [(c.category_id, c.name_filter, c.match_exact) for c in request.categories or []],
        )
        model.set_size_options([(o.size_id, o.option_value_id) for o in request.size_options or []])
        await self._repository.save_changes()
        await self._storefront.push([production_id])

    async def add_component(self, production_id: int, request: SaveComponentRequest) -> int:
        await self._require_model(production_id)
        component = ProductionComponent(production_id, request.name, request.sort_order)
        self._repository.add(component)
        await self._repository.save_changes()
        return component.id

    async def update_component(self, component_id: int, request: SaveComponentRequest) -> None:
        (await self._require_component(component_id)).rename(request.name, request.sort_order)
        await self._repository.save_changes()

    async def delete_component(self, component_id: int) -> None:
        """
        Deletes a part with its cutting BOM, stock and sewing BOM rows. Unlike the old module, a part with a
        non-zero stock cannot be deleted, so nothing cut gets lost.
        """
        component = await self._require_component(component_id)
        stock = await self._repository.find_component_stock(component_id)
        if any(s.stock != 0 for s in stock):
            raise DomainError(f'Деталь "{component.name}" має розкроєний запас, її не можна видалити.')
        model = await self._require_model(component.production_id)
        model.remove_component_from_sewn_bom(component_id)
        for row in stock:
            await self._repository.remove(row)
        await self._repository.remove(component)
        await self._repository.save_changes()

    async def set_component_bom(self, component_id: int, request: SetComponentBomRequest) -> None:
        component = await self._require_component(component_id)
        model = await self._require_model(component.production_id)
        rows = [(r.size_id, r.item_id, r.qty_per_unit) for r in request.rows or []]
        await self._require_items([item_id for _, item_id, _ in rows])
        component.set_bom(rows, [s.id for s in model.sizes])
        await self._repository.save_changes()

    async def set_sewn_bom(self, production_id: int, request: SetSewnBomRequest) -> None:
        model = await self._require_model(production_id)
        rows = [(r.size_id, r.component_id, r.item_id, r.qty_per_unit) for r in request.rows or []]
        await self._require_items([item_id for _, _, item_id, _ in rows if item_id is not None])
        model.set_sewn_bom(rows, await self._repository.component_ids(production_id))
        await self._repository.save_changes()

    async def _load_consumption(self, model: ProductionModel) -> "_Consumption":
        components = await self._repository.find_components(model.id)
        item_ids = list(
            dict.fromkeys(
                [b.item_id for c in components for b in c.bom]
                + [b.item_id for b in model.sewn_bom if b.item_id is not None]
            )
        )
        items = await self._repository.find_stock_items(item_ids)
        stocks = await self._repository.find_component_stocks([c.id for c in components])
        return _Consumption(self._repository, model, components, items, stocks)

    async def _require_items(self, item_ids: list[int]) -> None:
        missing = await self._repository.missing_stock_item_ids(list(dict.fromkeys(item_ids)))
        if missing:
            raise DomainError(f"Розхідників {', '.join(map(str, missing))} немає на складі.")

    async def _require_model(self, production_id: int) -> ProductionModel:
        model = await self._repository.find_model(production_id)
        if model is None or not model.is_active:
            raise NotFoundError(f"Модель {production_id} не знайдено.")
        return model

    async def _require_component(self, component_id: int) -> ProductionComponent:
        component = await self._repository.find_component(component_id)
        if component is None:
            raise NotFoundError(f"Деталь {component_id} не знайдено.")
        return component


class _Consumption:
    """Writing off consumables and parts for one operation (state shared by all sizes of the request)."""

    def __init__(
        self,
        repository: ProductionRepository,
        model: ProductionModel,
        components: list[ProductionComponent],
        items: list[StockItem],
        stocks: list[ComponentStock],
    ) -> None:
        self._repository = repository
        self._model = model
        self._components = components
        self._items = {i.id: i for i in items}
        self._stocks = {(s.component_id, s.size_id): s for s in stocks}
        self.warnings: list[str] = []

    def for_cut(self, size_name: str, size_id: int, quantity: Decimal, user_id: int, username: str, now: datetime):
        """ "+Покроєно": every part gets +quantity, and its consumables are written off by the cutting BOM."""
        for component in self._components:
            for line in resolve_bom(component.bom, size_id, lambda b: b.size_id, lambda b: b.item_id):
                comment = f"Крій: {self._model.name}, {component.name}, {size_name} x{plain(quantity, 3)}"
                self._take_item(line.item_id, line.qty_per_unit * quantity, comment, user_id, username, now)
            self._stock_of(component.id, size_id).add(quantity)

    def for_sewing(self, size_name: str, size_id: int, quantity: Decimal, user_id: int, username: str, now: datetime):
        """ "+Пошито": cut parts come from their stock, direct consumables (threads, labels) from the warehouse."""
        for line in resolve_bom(self._model.sewn_bom, size_id, lambda b: b.size_id, lambda b: b.component_id):
            need = line.qty_per_unit * quantity
            taken = self._stock_of(line.component_id, size_id).take_up_to(need)
            if taken < need:
                name = next(
                    (c.name for c in self._components if c.id == line.component_id), f"деталь {line.component_id}"
                )
                self.warnings.append(
                    f"Бракує розкроєних деталей «{name}» розміру {size_name}: {plain(need - taken, 3)} шт. "
                    f"Взято {plain(taken, 3)} з {plain(need, 3)}."
                )
        for line in resolve_bom(self._model.sewn_bom, size_id, lambda b: b.size_id, lambda b: b.item_id):
            comment = f"Пошив: {self._model.name}, {size_name} x{plain(quantity, 3)}"
            self._take_item(line.item_id, line.qty_per_unit * quantity, comment, user_id, username, now)

    def _take_item(self, item_id: int, need: Decimal, comment: str, user_id: int, username: str, now: datetime):
        item = self._items.get(item_id)
        if item is None:
            self.warnings.append(f"Розхідника {item_id} зі специфікації немає на складі, його не списано.")
            return
        # Long model and part names may not fit into the log comment (255 characters).
        movement = item.take_up_to(need, comment[:255], user_id, username, now)
        if movement is not None:
            self._repository.add(movement)
        taken = movement.quantity if movement else Decimal(0)
        if taken < need:
            self.warnings.append(
                f"Бракує {plain(need - taken, 3)} {item.unit}: {item.name}. "
                f"Списано {plain(taken, 3)} з {plain(need, 3)}."
            )

    def _stock_of(self, component_id: int, size_id: int) -> ComponentStock:
        stock = self._stocks.get((component_id, size_id))
        if stock is None:
            stock = ComponentStock(component_id, size_id)
            self._stocks[(component_id, size_id)] = stock
            self._repository.add(stock)
        return stock


class OrderSewnStockHandler:
    """
    Sewn pieces and orders (the old site's StockTrack event). When an order enters the statuses of
    production.order_subtract_status_ids, pieces of the needed size are written off; when it leaves, they come back.
    The size comes from the order line's option linked to a model size.
    """

    def __init__(
        self,
        repository: ProductionRepository,
        storefront: StorefrontStock,
        subtract_status_ids: Sequence[int],
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._storefront = storefront
        self._statuses = set(subtract_status_ids)
        self._clock = clock

    async def on_status_changing(self, change: OrderStatusChange) -> list[str]:
        was_in = change.old_status_id in self._statuses
        is_in = change.new_status_id in self._statuses
        if was_in == is_in:
            return []

        lines = [line for line in await self._repository.get_order_size_lines(change.order_id) if line.production_id]
        if not lines:
            return []
        models = {m.id: m for m in await self._repository.find_models(list({line.production_id for line in lines}))}

        warnings: list[str] = []
        by_size: dict[tuple[ProductionModel, ProductionSize], Decimal] = {}
        for line in lines:
            model = models.get(line.production_id)
            if model is None:
                continue
            size = _size_of(model, line.option_value_ids)
            if size is None:
                warnings.append(
                    f"«{line.product_name}»: розмір із замовлення не зв'язаний з розмірами моделі «{model.name}», "
                    "склад не змінено."
                )
                continue
            by_size[(model, size)] = by_size.get((model, size), Decimal(0)) + line.quantity

        # What the order already holds by the log. So a repeated call (a failure after the write-off but before the
        # status was saved, or a write-off the old site already did) neither takes nor returns twice.
        held = await self._repository.get_sewn_held_by_order(change.order_id)
        now = self._clock.now()
        for (model, size), quantity in by_size.items():
            if is_in:
                missing = quantity - held.get(size.id, Decimal(0))
                if missing <= 0:
                    continue
                entry = model.take_sewn_for_order(
                    size.id, missing, change.order_id, change.user_id, SYSTEM_USERNAME, now
                )
                if entry is not None:
                    self._repository.add(entry)
                taken = entry.quantity if entry else Decimal(0)
                if taken < missing:
                    warnings.append(
                        f"Пошитих «{model.name}» розміру {size.name} бракує {plain(missing - taken, 3)} шт.: "
                        f"списано {plain(taken, 3)} з {plain(missing, 3)}."
                    )
            else:
                # Returns not more than the order really holds. The old module returned the full quantity even when
                # pieces were short at the write-off, and so "made" pieces that did not exist.
                to_return = min(quantity, held.get(size.id, Decimal(0)))
                if to_return > 0:
                    self._repository.add(
                        model.return_sewn_from_order(
                            size.id, to_return, change.order_id, change.user_id, SYSTEM_USERNAME, now
                        )
                    )
        await self._repository.save_changes()
        # The site lowers the stock itself only at checkout, so after a change in NewErp it is pushed.
        await self._storefront.push(list({model.id for model, _ in by_size}))
        return warnings


class SewingPlanService:
    """How many of each model size to sew and cut for active orders."""

    def __init__(self, source: SewingPlanSource, plan_status_ids: Sequence[int], clock: Clock) -> None:
        self._source = source
        self._status_ids = list(plan_status_ids)
        self._clock = clock

    async def get(self, only_to_sew: bool) -> SewingPlanDto:
        orders = await self._source.get_orders(self._status_ids)
        lines = [(order, line) for order in orders for line in order.lines]
        models = {
            m.id: m
            for m in await self._source.get_models(
                list({line.production_id for _, line in lines if line.production_id is not None})
            )
        }
        days = await self._source.get_production_days(list({line.product_id for _, line in lines}))

        issues: list[tuple[str, str, int]] = []
        demands: list[SewingDemand] = []
        # As in the stock handler: the line's size is the model size linked to one of its option values.
        for order, line in lines:
            model = models.get(line.production_id) if line.production_id is not None else None
            if model is None:
                issues.append((line.product_name, "Не прив'язано до моделі виробництва.", order.order_id))
                continue
            size = _size_of(model, line.option_value_ids)
            if size is None:
                reason = f"Розмір із замовлення не зв'язаний з розмірами моделі «{model.name}»."
                issues.append((line.product_name, reason, order.order_id))
                continue
            due = deadline(order.date_added, days.get(line.product_id, 0))
            demands.append(SewingDemand(order.order_id, size.id, Decimal(line.quantity), Decimal(0), due))

        # What an order holds counts per size, not per line: it is spread over the lines of that size in turn.
        demands = _spread_held(demands, {o.order_id: dict(o.held) for o in orders})
        today = self._clock.now().date()
        rows = []
        for model in models.values():
            for size in sorted(model.sizes, key=lambda s: (s.sort_order, s.id)):
                if not any(d.size_id == size.id for d in demands):
                    continue
                plan = plan_for_size(size.id, size.sewn_stock, size.cut_stock, demands)
                if only_to_sew and plan.to_sew <= 0:
                    continue
                rows.append(
                    SewingPlanRowDto(
                        production_id=model.id,
                        model_name=model.name,
                        size_id=size.id,
                        size_name=size.name,
                        ordered=plan.ordered,
                        missing=plan.missing,
                        sewn_stock=size.sewn_stock,
                        cut_stock=size.cut_stock,
                        to_sew=plan.to_sew,
                        from_cut=plan.from_cut,
                        to_cut=plan.to_cut,
                        deadline=plan.deadline,
                        overdue=plan.to_sew > 0 and plan.deadline is not None and plan.deadline < today,
                        order_ids=plan.order_ids,
                    )  # fmt: skip
                )
        rows.sort(
            key=lambda r: (
                r.deadline is None,
                r.deadline or today,
                models[r.production_id].sort_order,
                ukrainian_key(r.model_name),
            )
        )

        # There are many products that are not sewn (no model) in active orders, so they are grouped by product.
        grouped: dict[tuple[str, str], set[int]] = {}
        for product, reason, order_id in issues:
            grouped.setdefault((product, reason), set()).add(order_id)
        issue_rows = [
            SewingPlanIssueDto(product_name=product, reason=reason, order_ids=sorted(order_ids))
            for (product, reason), order_ids in grouped.items()
        ]
        issue_rows.sort(key=lambda i: ukrainian_key(i.product_name))
        return SewingPlanDto(order_count=len(orders), rows=rows, issues=issue_rows)


def _spread_held(demands: list[SewingDemand], held: dict[int, dict[int, Decimal]]) -> list[SewingDemand]:
    result = []
    for demand in demands:
        sizes = held.get(demand.order_id)
        available = sizes.get(demand.size_id, Decimal(0)) if sizes else Decimal(0)
        take = min(max(available, Decimal(0)), demand.ordered)
        if sizes is not None and take > 0:
            sizes[demand.size_id] = available - take
        result.append(demand.with_held(take))
    return result


class ProductionLocationService:
    """Sewn pieces at several warehouses; what lies at no warehouse is the main one."""

    def __init__(
        self,
        production: ProductionRepository,
        locations: SewnLocationRepository,
        warehouses: LocationStore,
        clock: Clock,
    ) -> None:
        self._production = production
        self._locations = locations
        self._warehouses = warehouses
        self._clock = clock

    async def get(self, production_id: int) -> list[SizePlacesDto]:
        model = await self._production.get_dto(production_id)
        if model is None:
            raise _model_not_found(production_id)
        balances = await self._locations.find_balances(production_id)
        names = await self._names()
        return [
            _size_places(s.id, s.name, s.sewn_stock, [b for b in balances if b.size_id == s.id], names)
            for s in model.sizes
        ]

    async def transfer(
        self, production_id: int, request: TransferSewnRequest, user_id: int, username: str
    ) -> list[SizePlacesDto]:
        """Moves a size's sewn pieces between places; the sewn stock and the store do not change."""
        names = await self._names()
        require_warehouse(request.from_warehouse_id, names)
        require_warehouse(request.to_warehouse_id, names)
        model = await self._production.find_model(production_id)
        if model is None:
            raise _model_not_found(production_id)
        placement = SewnPlacement(model, await self._locations.find_balances(production_id))
        transfer = placement.move(
            request.size_id, request.from_warehouse_id, request.to_warehouse_id, request.quantity, request.comment,
            user_id, username, self._clock.now(),
        )  # fmt: skip
        for balance in placement.created:
            self._locations.add(balance)
        self._locations.add(transfer)
        await self._production.save_changes()
        return [
            _size_places(s.id, s.name, s.sewn_stock, placement.balances(s.id), names)
            for s in sorted(model.sizes, key=lambda s: s.sort_order)
        ]

    async def transfers(self, production_id: int) -> list[SewnTransferDto]:
        names = await self._names()
        model = await self._production.get_dto(production_id)
        sizes = {s.id: s.name for s in model.sizes} if model else {}
        return [
            SewnTransferDto(
                id=t.id,
                size_id=t.size_id,
                size_name=sizes.get(t.size_id, ""),
                from_warehouse_id=t.from_warehouse_id,
                from_=place_name(t.from_warehouse_id, names),
                to_warehouse_id=t.to_warehouse_id,
                to=place_name(t.to_warehouse_id, names),
                quantity=t.quantity,
                comment=t.comment,
                username=t.username,
                created_at=t.created_at,
            )  # fmt: skip
            for t in await self._locations.list_transfers(production_id)
        ]

    async def by_warehouse(self) -> SewnByWarehouseDto:
        """Sizes of active models with pieces in each place; only warehouses holding something get a column."""
        names = await self._names()
        by_size: dict[int, dict[int, Decimal]] = {}
        for row in await self._locations.list_balances():
            by_size.setdefault(row.size_id, {})[row.warehouse_id] = row.quantity
        columns = sorted({w for at in by_size.values() for w in at}, key=lambda w: ukrainian_key(place_name(w, names)))
        places = [SewnByWarehousePlace(warehouse_id=None, name=MAIN_NAME)]
        places += [SewnByWarehousePlace(warehouse_id=w, name=place_name(w, names)) for w in columns]
        rows = []
        for s in await self._locations.list_active_sizes():
            at = by_size.get(s.size_id, {})
            quantities = [s.sewn - sum(at.values(), Decimal(0))] + [at.get(w, Decimal(0)) for w in columns]
            rows.append(
                SewnByWarehouseRow(
                    production_id=s.production_id,
                    model_name=s.model_name,
                    size_id=s.size_id,
                    size_name=s.size_name,
                    total=s.sewn,
                    quantities=quantities,
                )  # fmt: skip
            )
        return SewnByWarehouseDto(places=places, rows=rows)

    async def _names(self) -> dict[int, str]:
        return {w.id: w.name for w in await self._warehouses.list_all(None)}


def _size_places(
    size_id: int, size_name: str, sewn: Decimal, balances: list[SewnBalance], names: dict[int, str]
) -> SizePlacesDto:
    places = [
        PlaceDto(warehouse_id=None, name=MAIN_NAME, quantity=sewn - sum((b.quantity for b in balances), Decimal(0)))
    ]
    places += [
        PlaceDto(warehouse_id=b.warehouse_id, name=place_name(b.warehouse_id, names), quantity=b.quantity)
        for b in sorted(
            (b for b in balances if b.quantity != 0), key=lambda b: ukrainian_key(place_name(b.warehouse_id, names))
        )
    ]
    return SizePlacesDto(size_id=size_id, size_name=size_name, sewn=sewn, places=places)


def _size_of(model: ProductionModel, option_value_ids: list[int]) -> ProductionSize | None:
    return next(
        (s for s in model.sizes if s.option_value_id is not None and s.option_value_id in option_value_ids), None
    )


def _change_dto(size_id: int, entry) -> SizeChangeDto:
    return SizeChangeDto(size_id=size_id, size_name=entry.size_name, before=entry.stock_before, after=entry.stock_after)


def _sizes(request: SaveProductionModelRequest) -> list[tuple[str | None, Decimal]]:
    return [(s.name, s.percent) for s in request.sizes or []]


def _require_unique(values: list[int], message: str) -> None:
    seen: set[int] = set()
    for value in values:
        if value in seen:
            raise DomainError(message.format(value))
        seen.add(value)


def _model_not_found(production_id: int) -> NotFoundError:
    return NotFoundError(f"Модель {production_id} не знайдено.")
