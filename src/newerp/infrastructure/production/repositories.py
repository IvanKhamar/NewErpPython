from collections import defaultdict
from decimal import Decimal

from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.common import ukrainian_key
from newerp.application.production.models import (
    ActiveSizeRow,
    CategoryLinkDto,
    ComponentBomLineDto,
    ComponentDto,
    OrderSizeLine,
    PlanOrder,
    ProductionLogDto,
    ProductionLogFilter,
    ProductionModelDto,
    ProductionModelSummaryDto,
    ProductionSizeDto,
    SewnBalanceRow,
    SewnBomLineDto,
    StorefrontSyncResult,
)
from newerp.application.production.ports import ProductionModelFilter
from newerp.domain.production import (
    IN,
    OUT,
    SEWN,
    ComponentStock,
    ProductionComponent,
    ProductionModel,
    SewnBalance,
    SewnTransfer,
    cancel_comment,
    order_comment,
)
from newerp.domain.stock import StockItem
from newerp.infrastructure.models.catalog import (
    DdCategory,
    DdProduct,
    DdProductDescription,
    DdProductOptionValue,
    DdProductToCategory,
)
from newerp.infrastructure.models.orders import DdOrder, DdOrderOption, DdOrderProduct
from newerp.infrastructure.production.mapping import (
    category_links,
    component_bom,
    component_stock,
    components,
    models,
    product_links,
    production_log,
    sewn_locations,
    sewn_transfers,
    sizes,
)
from newerp.infrastructure.stock.mapping import stock_items


async def product_owners(
    session: AsyncSession, language_id: int, product_ids: list[int] | None = None
) -> dict[int, int]:
    """
    Which model each store product belongs to. A direct link wins over a category, and a category with a name
    filter over a whole category (smaller model id first). Through a category only enabled products count when
    product_ids is None (the storefront push); for given products (orders) the status does not matter.
    Like the old module, names are compared case-insensitively (the database collation).
    """
    direct_query = (
        select(product_links.c.product_id, product_links.c.production_id)
        .join(models, models.c.production_id == product_links.c.production_id)
        .where(models.c.status.is_(True))
    )
    category_query = (
        select(DdProductToCategory.product_id, category_links.c.production_id, category_links.c.name_filter != "")
        .select_from(category_links)
        .join(models, models.c.production_id == category_links.c.production_id)
        .join(DdProductToCategory, DdProductToCategory.category_id == category_links.c.oc_category_id)
        .join(
            DdProductDescription,
            and_(
                DdProductDescription.product_id == DdProductToCategory.product_id,
                DdProductDescription.language_id == language_id,
            ),
        )
        .where(
            models.c.status.is_(True),
            or_(
                category_links.c.name_filter == "",
                and_(category_links.c.match_exact.is_(True), DdProductDescription.name == category_links.c.name_filter),
                and_(
                    category_links.c.match_exact.is_(False),
                    DdProductDescription.name.contains(category_links.c.name_filter),
                ),
            ),
        )
    )
    if product_ids is None:
        category_query = category_query.join(DdProduct, DdProduct.product_id == DdProductToCategory.product_id).where(
            DdProduct.status.is_(True)
        )
    else:
        direct_query = direct_query.where(product_links.c.product_id.in_(product_ids))
        category_query = category_query.where(DdProductToCategory.product_id.in_(product_ids))

    owners: dict[int, int] = {}
    by_category = sorted(await session.execute(category_query), key=lambda row: (not row[2], row[1]))
    for product_id, production_id, _ in by_category:
        owners.setdefault(product_id, production_id)
    direct: dict[int, int] = {}
    for product_id, production_id in await session.execute(direct_query):
        direct[product_id] = min(production_id, direct.get(product_id, production_id))
    return owners | direct


class SqlProductionRepository:
    """The production repository and the sewing plan source (one class, like ProductionRepository in .NET)."""

    def __init__(self, session: AsyncSession, language_id: int) -> None:
        self._session = session
        self._language_id = language_id

    # ── Orders ──

    async def get_order_size_lines(self, order_id: int) -> list[OrderSizeLine]:
        return [line for _, line in await self._size_lines([order_id])]

    async def _size_lines(self, order_ids: list[int]) -> list[tuple[int, OrderSizeLine]]:
        """Order lines with their model and option values (for several orders at once)."""
        products = (
            await self._session.execute(
                select(
                    DdOrderProduct.order_id,
                    DdOrderProduct.order_product_id,
                    DdOrderProduct.product_id,
                    DdOrderProduct.name,
                    DdOrderProduct.quantity,
                )
                .where(DdOrderProduct.order_id.in_(order_ids))
                .order_by(DdOrderProduct.order_id, DdOrderProduct.order_product_id)
            )
        ).all()
        if not products:
            return []
        product_ids = list({p.product_id for p in products})
        # An order option refers to a product_option_value; a model size refers to the option_value_id from it.
        options: dict[int, list[int]] = defaultdict(list)
        for line_id, option_value_id in await self._session.execute(
            select(DdOrderOption.order_product_id, DdProductOptionValue.option_value_id)
            .join(
                DdProductOptionValue,
                DdProductOptionValue.product_option_value_id == DdOrderOption.product_option_value_id,
            )
            .where(DdOrderOption.order_id.in_(order_ids))
        ):
            options[line_id].append(option_value_id)
        owners = await product_owners(self._session, self._language_id, product_ids)
        return [
            (
                p.order_id,
                OrderSizeLine(
                    product_id=p.product_id,
                    product_name=p.name,
                    quantity=p.quantity,
                    production_id=owners.get(p.product_id),
                    option_value_ids=options[p.order_product_id],
                ),
            )
            for p in products
        ]

    async def get_orders(self, status_ids: list[int]) -> list[PlanOrder]:
        if not status_ids:
            return []
        orders = (
            await self._session.execute(
                select(DdOrder.order_id, DdOrder.date_added)
                .where(DdOrder.order_status_id.in_(status_ids))
                .order_by(DdOrder.order_id)
            )
        ).all()
        if not orders:
            return []
        ids = [o.order_id for o in orders]
        lines: dict[int, list[OrderSizeLine]] = defaultdict(list)
        for order_id, line in await self._size_lines(ids):
            lines[order_id].append(line)
        held = await self._sewn_held(ids)
        return [PlanOrder(o.order_id, o.date_added, lines[o.order_id], held.get(o.order_id, {})) for o in orders]

    async def get_production_days(self, product_ids: list[int]) -> dict[int, int]:
        if not product_ids:
            return {}
        rows = await self._session.execute(
            select(DdProductToCategory.product_id, DdProductToCategory.category_id, DdCategory.production_period)
            .join(DdCategory, DdCategory.category_id == DdProductToCategory.category_id)
            .where(DdProductToCategory.product_id.in_(product_ids))
        )
        first: dict[int, tuple[int, int]] = {}
        for product_id, category_id, days in rows:
            if product_id not in first or category_id < first[product_id][0]:
                first[product_id] = (category_id, days)
        return {product_id: days for product_id, (_, days) in first.items()}

    async def get_models(self, production_ids: list[int]) -> list[ProductionModel]:
        return await self.find_models(production_ids)

    async def _sewn_held(self, order_ids: list[int]) -> dict[int, dict[int, Decimal]]:
        """What get_sewn_held_by_order gives, for many orders in one query."""
        by_comment = {order_comment(i): (i, True) for i in order_ids} | {
            cancel_comment(i): (i, False) for i in order_ids
        }
        log = production_log.c
        rows = await self._session.execute(
            select(log.size_id, log.direction, log.comment, log.quantity).where(
                log.stage == SEWN, log.comment.in_(list(by_comment))
            )
        )
        held: dict[int, dict[int, Decimal]] = defaultdict(dict)
        for size_id, direction, comment, quantity in rows:
            order_id, taken = by_comment[comment]
            if direction != (OUT if taken else IN):
                continue
            sizes_of_order = held[order_id]
            sizes_of_order[size_id] = sizes_of_order.get(size_id, Decimal(0)) + (quantity if taken else -quantity)
        return held

    async def find_models(self, production_ids: list[int]) -> list[ProductionModel]:
        if not production_ids:
            return []
        return list(
            await self._session.scalars(select(ProductionModel).where(models.c.production_id.in_(production_ids)))
        )

    async def get_sewn_held_by_order(self, order_id: int) -> dict[int, Decimal]:
        return (await self._sewn_held([order_id])).get(order_id, {})

    # ── Models ──

    async def list_models(self, model_filter: ProductionModelFilter) -> list[ProductionModelSummaryDto]:
        m, s = models.c, sizes.c
        query = (
            select(
                m.production_id,
                m.name,
                m.type,
                m.min_total,
                func.coalesce(func.sum(s.cut_stock), 0),
                func.coalesce(func.sum(s.sewn_stock), 0),
            )  # fmt: skip
            .select_from(models)
            .outerjoin(sizes, s.production_id == m.production_id)
            .where(m.status.is_(True))
            .group_by(m.production_id, m.name, m.type, m.min_total, m.sort_order)
            .order_by(m.sort_order, m.name)
        )
        if model_filter.type:
            query = query.where(m.type == model_filter.type)
        result = []
        for production_id, name, model_type, min_total, cut, sewn in await self._session.execute(query):
            deficit = max(Decimal(0), Decimal(min_total) - Decimal(sewn))
            if model_filter.below_minimum_only and deficit <= 0:
                continue
            result.append(
                ProductionModelSummaryDto(
                    id=production_id,
                    name=name,
                    type=model_type,
                    min_total=min_total,
                    total_cut=cut,
                    total_sewn=sewn,
                    deficit=deficit,
                )  # fmt: skip
            )
        return result

    async def get_dto(self, production_id: int) -> ProductionModelDto | None:
        model = await self.find_model(production_id)
        if model is None or not model.is_active:
            return None
        parts = await self.find_components(production_id)
        stock = await self.find_component_stocks([c.id for c in parts])
        item_ids = list(
            {b.item_id for c in parts for b in c.bom} | {b.item_id for b in model.sewn_bom if b.item_id is not None}
        )
        items = {
            item_id: (name, unit)
            for item_id, name, unit in await self._session.execute(
                select(stock_items.c.item_id, stock_items.c.name, stock_items.c.unit).where(
                    stock_items.c.item_id.in_(item_ids)
                )
            )
        }
        names = {c.id: c.name for c in parts}
        return ProductionModelDto(
            id=model.id,
            name=model.name,
            type=model.type,
            min_total=model.min_total,
            sort_order=model.sort_order,
            sizes=[
                ProductionSizeDto(
                    id=s.id,
                    name=s.name,
                    percent=s.percent,
                    cut_stock=s.cut_stock,
                    sewn_stock=s.sewn_stock,
                    option_value_id=s.option_value_id,
                )  # fmt: skip
                for s in sorted(model.sizes, key=lambda s: s.sort_order)
            ],
            product_ids=sorted(link.product_id for link in model.product_links),
            categories=[
                CategoryLinkDto(category_id=c.category_id, name_filter=c.name_filter, match_exact=c.match_exact)
                for c in model.category_links
            ],
            components=[
                ComponentDto(
                    id=c.id,
                    name=c.name,
                    sort_order=c.sort_order,
                    stock={s.size_id: s.stock for s in stock if s.component_id == c.id},
                    bom=[
                        ComponentBomLineDto(
                            size_id=b.size_id,
                            item_id=b.item_id,
                            item_name=items.get(b.item_id, (None, None))[0],
                            unit=items.get(b.item_id, (None, None))[1],
                            qty_per_unit=b.qty_per_unit,
                        )  # fmt: skip
                        for b in c.bom
                    ],
                )
                for c in parts
            ],
            sewn_bom=[
                SewnBomLineDto(
                    size_id=b.size_id,
                    component_id=b.component_id,
                    component_name=names.get(b.component_id) if b.component_id is not None else None,
                    item_id=b.item_id,
                    item_name=items.get(b.item_id, (None, None))[0] if b.item_id is not None else None,
                    unit=items.get(b.item_id, (None, None))[1] if b.item_id is not None else None,
                    qty_per_unit=b.qty_per_unit,
                )
                for b in model.sewn_bom
            ],
        )

    async def find_model(self, production_id: int) -> ProductionModel | None:
        return await self._session.get(ProductionModel, production_id)

    async def find_component(self, component_id: int) -> ProductionComponent | None:
        return await self._session.get(ProductionComponent, component_id)

    async def component_ids(self, production_id: int) -> list[int]:
        return list(
            await self._session.scalars(
                select(components.c.component_id).where(components.c.production_id == production_id)
            )
        )

    async def missing_stock_item_ids(self, item_ids: list[int]) -> list[int]:
        if not item_ids:
            return []
        existing = set(
            await self._session.scalars(
                select(stock_items.c.item_id).where(stock_items.c.status.is_(True), stock_items.c.item_id.in_(item_ids))
            )
        )
        return [i for i in item_ids if i not in existing]

    async def sizes_in_use(self, size_ids: list[int]) -> bool:
        in_bom = await self._session.scalar(select(exists().where(component_bom.c.size_id.in_(size_ids))))
        in_stock = await self._session.scalar(
            select(exists().where(component_stock.c.size_id.in_(size_ids), component_stock.c.stock != 0))
        )
        return bool(in_bom or in_stock)

    async def find_component_stock(self, component_id: int) -> list[ComponentStock]:
        return list(
            await self._session.scalars(select(ComponentStock).where(component_stock.c.component_id == component_id))
        )

    async def find_components(self, production_id: int) -> list[ProductionComponent]:
        rows = await self._session.scalars(
            select(ProductionComponent).where(components.c.production_id == production_id)
        )
        return sorted(rows, key=lambda c: (c.sort_order, ukrainian_key(c.name)))

    async def find_component_stocks(self, component_ids: list[int]) -> list[ComponentStock]:
        if not component_ids:
            return []
        return list(
            await self._session.scalars(select(ComponentStock).where(component_stock.c.component_id.in_(component_ids)))
        )

    async def find_stock_items(self, item_ids: list[int]) -> list[StockItem]:
        if not item_ids:
            return []
        return list(
            await self._session.scalars(
                select(StockItem).where(stock_items.c.status.is_(True), stock_items.c.item_id.in_(item_ids))
            )
        )

    async def list_log(self, log_filter: ProductionLogFilter) -> list[ProductionLogDto]:
        log, m = production_log.c, models.c
        query = (
            select(
                log.log_id,
                log.production_id,
                m.name,
                log.size_id,
                log.size_name,
                log.stage,
                log.direction,
                log.quantity,
                log.stock_before,
                log.stock_after,
                log.comment,
                log.user_id,
                log.username,
                log.date_added,
            )  # fmt: skip
            .select_from(production_log)
            .outerjoin(models, m.production_id == log.production_id)
            .order_by(log.date_added.desc(), log.log_id.desc())
            .limit(min(max(log_filter.limit, 1), 500))
        )
        if log_filter.production_id is not None:
            query = query.where(log.production_id == log_filter.production_id)
        if log_filter.stage:
            query = query.where(log.stage == log_filter.stage)
        return [
            ProductionLogDto(
                id=r[0],
                production_id=r[1],
                production_name=r[2],
                size_id=r[3],
                size_name=r[4],
                stage=r[5],
                direction=r[6],
                quantity=r[7],
                stock_before=r[8],
                stock_after=r[9],
                comment=r[10],
                user_id=r[11],
                username=r[12],
                created_at=r[13],
            )  # fmt: skip
            for r in await self._session.execute(query)
        ]

    def add(self, entity: object) -> None:
        self._session.add(entity)

    async def remove(self, entity: object) -> None:
        await self._session.delete(entity)

    async def save_changes(self) -> None:
        await self._session.commit()


class SqlSewnLocationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_balances(self, production_id: int) -> list[SewnBalance]:
        of_model = select(sizes.c.size_id).where(sizes.c.production_id == production_id)
        return list(await self._session.scalars(select(SewnBalance).where(sewn_locations.c.size_id.in_(of_model))))

    async def list_balances(self) -> list[SewnBalanceRow]:
        b = sewn_locations.c
        rows = await self._session.execute(select(b.size_id, b.warehouse_id, b.quantity).where(b.quantity != 0))
        return [SewnBalanceRow(*row) for row in rows]

    async def list_active_sizes(self) -> list[ActiveSizeRow]:
        m, s = models.c, sizes.c
        rows = await self._session.execute(
            select(m.production_id, m.name, s.size_id, s.size_name, s.sewn_stock)
            .join(sizes, s.production_id == m.production_id)
            .where(m.status.is_(True))
            .order_by(m.sort_order, m.name, s.sort_order)
        )
        return [ActiveSizeRow(*row) for row in rows]

    async def list_transfers(self, production_id: int) -> list[SewnTransfer]:
        t = sewn_transfers.c
        return list(
            await self._session.scalars(
                select(SewnTransfer).where(t.production_id == production_id).order_by(t.created_at.desc(), t.id.desc())
            )
        )

    async def holds_stock(self, warehouse_id: int) -> bool:
        b = sewn_locations.c
        return bool(await self._session.scalar(select(exists().where(b.warehouse_id == warehouse_id, b.quantity != 0))))

    def add(self, entity: object) -> None:
        self._session.add(entity)


class SqlStorefrontStock:
    """Pushes sewn stock to OpenCart with direct UPDATEs (MyISAM, no transactions), like the old module."""

    def __init__(self, session: AsyncSession, language_id: int, settle_pre_orders=None) -> None:
        self._session = session
        self._language_id = language_id
        # A negative stock is sold in advance: it is first taken off the pre-order limit, otherwise the new stock
        # would wipe it while the limit stays full. Wired when the pre-orders module is ported.
        self._settle_pre_orders = settle_pre_orders

    async def push(self, production_ids: list[int] | None) -> StorefrontSyncResult:
        if production_ids is not None and not production_ids:
            return StorefrontSyncResult(models=0, products=0)
        query = select(ProductionModel).where(models.c.status.is_(True))
        if production_ids is not None:
            query = query.where(models.c.production_id.in_(production_ids))
        active = list(await self._session.scalars(query))
        owners = await product_owners(self._session, self._language_id)

        model_count = product_count = 0
        for model in active:
            quantities = model.storefront_quantities()
            product_ids = [product_id for product_id, owner in owners.items() if owner == model.id]
            if not quantities or not product_ids:
                continue
            if self._settle_pre_orders is not None:
                await self._settle_pre_orders(product_ids)
            for option_value_id, quantity in quantities.items():
                await self._session.execute(
                    update(DdProductOptionValue)
                    .where(
                        DdProductOptionValue.product_id.in_(product_ids),
                        DdProductOptionValue.option_value_id == option_value_id,
                    )
                    .values(quantity=quantity)
                )
            # A product's quantity is the sum of its linked sizes (only products that have such sizes).
            totals = (
                await self._session.execute(
                    select(DdProductOptionValue.product_id, func.sum(DdProductOptionValue.quantity))
                    .where(
                        DdProductOptionValue.product_id.in_(product_ids),
                        DdProductOptionValue.option_value_id.in_(list(quantities)),
                    )
                    .group_by(DdProductOptionValue.product_id)
                )
            ).all()
            for product_id, total in totals:
                await self._session.execute(
                    update(DdProduct).where(DdProduct.product_id == product_id).values(quantity=int(total))
                )
            await self._session.commit()
            model_count += 1
            product_count += len(totals)
        return StorefrontSyncResult(models=model_count, products=product_count)
