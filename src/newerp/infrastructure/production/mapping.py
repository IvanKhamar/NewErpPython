"""Maps the plain production domain classes to the reconstructed tables of the old StockTrack module."""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String, Table
from sqlalchemy.orm import relationship

from newerp.domain.production import (
    ComponentBomLine,
    ComponentStock,
    ProductionCategoryLink,
    ProductionComponent,
    ProductionLogEntry,
    ProductionModel,
    ProductionProductLink,
    ProductionSize,
    SewnBalance,
    SewnBomLine,
    SewnTransfer,
)
from newerp.infrastructure.db import Base

metadata = Base.metadata
Quantity = Numeric(12, 3)
PerUnit = Numeric(12, 4)

models = Table(
    "dd_stock_production",
    metadata,
    Column("production_id", Integer, primary_key=True),
    Column("name", String(150)),
    Column("type", String(32)),
    Column("min_total", Integer),
    Column("sort_order", Integer),
    Column("status", Boolean),
)

sizes = Table(
    "dd_stock_production_sizes",
    metadata,
    Column("size_id", Integer, primary_key=True),
    Column("production_id", Integer, ForeignKey("dd_stock_production.production_id")),
    Column("size_name", String(32)),
    Column("percent", Numeric(5, 2)),
    Column("cut_stock", Quantity),
    Column("sewn_stock", Quantity),
    Column("sort_order", Integer),
    Column("oc_option_value_id", Integer, nullable=True),
)

product_links = Table(
    "dd_stock_production_products",
    metadata,
    Column("production_id", Integer, ForeignKey("dd_stock_production.production_id"), primary_key=True),
    Column("product_id", Integer, primary_key=True),
)

category_links = Table(
    "dd_stock_production_categories",
    metadata,
    Column("production_id", Integer, ForeignKey("dd_stock_production.production_id"), primary_key=True),
    Column("oc_category_id", Integer, primary_key=True),
    Column("name_filter", String(255)),
    Column("match_exact", Boolean),
)

sewn_bom = Table(
    "dd_stock_production_sewn_bom",
    metadata,
    Column("bom_id", Integer, primary_key=True),
    Column("production_id", Integer, ForeignKey("dd_stock_production.production_id")),
    Column("size_id", Integer, nullable=True),
    Column("component_id", Integer, nullable=True),
    Column("item_id", Integer, nullable=True),
    Column("qty_per_unit", PerUnit),
)

components = Table(
    "dd_stock_production_components",
    metadata,
    Column("component_id", Integer, primary_key=True),
    Column("production_id", Integer),
    Column("name", String(100)),
    Column("sort_order", Integer),
)

component_bom = Table(
    "dd_stock_production_component_bom",
    metadata,
    Column("bom_id", Integer, primary_key=True),
    Column("component_id", Integer, ForeignKey("dd_stock_production_components.component_id")),
    Column("size_id", Integer, nullable=True),
    Column("item_id", Integer),
    Column("qty_per_unit", PerUnit),
)

component_stock = Table(
    "dd_stock_production_component_stock",
    metadata,
    Column("component_id", Integer, primary_key=True),
    Column("size_id", Integer, primary_key=True),
    Column("stock", Quantity),
)

production_log = Table(
    "dd_stock_production_log",
    metadata,
    Column("log_id", Integer, primary_key=True),
    Column("production_id", Integer, ForeignKey("dd_stock_production.production_id")),
    Column("size_id", Integer, ForeignKey("dd_stock_production_sizes.size_id")),
    Column("size_name", String(32)),
    Column("user_id", Integer),
    Column("username", String(20)),
    Column("stage", String(8)),
    Column("direction", String(8)),
    Column("quantity", Quantity),
    Column("stock_before", Quantity),
    Column("stock_after", Quantity),
    Column("comment", String(255)),
    Column("date_added", DateTime),
)

sewn_locations = Table(
    "erp_production_location",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("size_id", Integer),
    Column("warehouse_id", Integer),
    Column("quantity", Quantity),
)

sewn_transfers = Table(
    "erp_production_transfer",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("production_id", Integer),
    Column("size_id", Integer),
    Column("from_warehouse_id", Integer, nullable=True),
    Column("to_warehouse_id", Integer, nullable=True),
    Column("quantity", Quantity),
    Column("comment", String(255)),
    Column("user_id", Integer),
    Column("username", String(20)),
    Column("created_at", DateTime),
)

mapper = Base.registry.map_imperatively


def _children(cls, order_by):
    """A collection owned by its parent: loaded with it, deleted when removed from it."""
    return relationship(cls, order_by=order_by, cascade="all, delete-orphan", lazy="selectin")


# Optimistic concurrency like IsConcurrencyToken in EF. SQLAlchemy checks one column per table, so sizes are checked
# by sewn_stock, the stock that orders and production both change.
mapper(
    ProductionSize,
    sizes,
    properties={
        "id": sizes.c.size_id,
        "name": sizes.c.size_name,
        "option_value_id": sizes.c.oc_option_value_id,
    },
    version_id_col=sizes.c.sewn_stock,
    version_id_generator=False,
)
mapper(ProductionProductLink, product_links)
mapper(ProductionCategoryLink, category_links, properties={"category_id": category_links.c.oc_category_id})
mapper(SewnBomLine, sewn_bom, properties={"id": sewn_bom.c.bom_id})
mapper(
    ProductionModel,
    models,
    properties={
        "id": models.c.production_id,
        "is_active": models.c.status,
        "_sizes": _children(ProductionSize, sizes.c.sort_order),
        "_product_links": _children(ProductionProductLink, product_links.c.product_id),
        "_category_links": _children(ProductionCategoryLink, category_links.c.oc_category_id),
        "_sewn_bom": _children(SewnBomLine, sewn_bom.c.bom_id),
    },
)
mapper(ComponentBomLine, component_bom, properties={"id": component_bom.c.bom_id})
mapper(
    ProductionComponent,
    components,
    properties={"id": components.c.component_id, "_bom": _children(ComponentBomLine, component_bom.c.bom_id)},
)
mapper(ComponentStock, component_stock, version_id_col=component_stock.c.stock, version_id_generator=False)
mapper(
    ProductionLogEntry,
    production_log,
    properties={
        "id": production_log.c.log_id,
        "created_at": production_log.c.date_added,
        "model": relationship(ProductionModel, lazy="raise"),
        "size": relationship(ProductionSize, lazy="raise"),
    },
)
mapper(SewnBalance, sewn_locations, version_id_col=sewn_locations.c.quantity, version_id_generator=False)
mapper(SewnTransfer, sewn_transfers)
