"""Maps the plain stock domain classes to tables (SQLAlchemy imperative mapping, like EF fluent configuration).

The tables of the old StockTrack module (dd_stock_*) were reconstructed (db/reconstructed.sql), so the column names
match the old PHP code. Purchase orders and warehouse balances are our own erp_* tables.
"""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String, Table, Text
from sqlalchemy.orm import relationship

from newerp.domain.stock import (
    PurchaseOrder,
    PurchaseOrderLine,
    StockBalance,
    StockCategory,
    StockItem,
    StockMovement,
    StockSupplier,
    StockTransfer,
)
from newerp.infrastructure.db import Base

metadata = Base.metadata
Quantity = Numeric(12, 3)
Price = Numeric(15, 4)

stock_categories = Table(
    "dd_stock_categories",
    metadata,
    Column("category_id", Integer, primary_key=True),
    Column("name", String(100)),
    Column("sort_order", Integer),
)

stock_suppliers = Table(
    "dd_stock_suppliers",
    metadata,
    Column("supplier_id", Integer, primary_key=True),
    Column("name", String(150)),
    Column("phone", String(32)),
    Column("email", String(96)),
    Column("categories", String(255)),
    Column("notes", Text),
)

stock_items = Table(
    "dd_stock_items",
    metadata,
    Column("item_id", Integer, primary_key=True),
    Column("category_id", Integer, nullable=True),
    Column("supplier_id", Integer, nullable=True),
    Column("name", String(150)),
    Column("unit", String(16)),
    Column("stock", Quantity),
    Column("min_stock", Quantity),
    Column("used_for", String(255)),
    Column("price", Price),
    Column("status", Boolean),
)

stock_log = Table(
    "dd_stock_log",
    metadata,
    Column("log_id", Integer, primary_key=True),
    Column("item_id", Integer, ForeignKey("dd_stock_items.item_id")),
    Column("item_name", String(150)),
    Column("action", String(16)),
    Column("quantity", Quantity),
    Column("stock_before", Quantity),
    Column("stock_after", Quantity),
    Column("comment", String(255)),
    Column("user_id", Integer),
    Column("username", String(20)),
    Column("date_added", DateTime),
)

purchase_orders = Table(
    "erp_purchase_order",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("supplier_id", Integer),
    Column("status", String(16)),
    Column("comment", String(500)),
    Column("created_by_user_id", Integer),
    Column("created_at", DateTime),
    Column("ordered_at", DateTime, nullable=True),
    Column("closed_at", DateTime, nullable=True),
)

purchase_order_lines = Table(
    "erp_purchase_order_line",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("order_id", Integer, ForeignKey("erp_purchase_order.id")),
    Column("item_id", Integer),
    Column("quantity", Quantity),
    Column("price", Price),
    Column("received_quantity", Quantity),
)

stock_locations = Table(
    "erp_stock_location",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("item_id", Integer),
    Column("warehouse_id", Integer),
    Column("quantity", Quantity),
)

stock_transfers = Table(
    "erp_stock_transfer",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("item_id", Integer),
    Column("from_warehouse_id", Integer, nullable=True),
    Column("to_warehouse_id", Integer, nullable=True),
    Column("quantity", Quantity),
    Column("comment", String(255)),
    Column("user_id", Integer),
    Column("username", String(20)),
    Column("created_at", DateTime),
)

mapper = Base.registry.map_imperatively

mapper(StockCategory, stock_categories, properties={"id": stock_categories.c.category_id})
mapper(StockSupplier, stock_suppliers, properties={"id": stock_suppliers.c.supplier_id})
mapper(
    StockItem,
    stock_items,
    properties={
        "id": stock_items.c.item_id,
        "quantity": stock_items.c.stock,
        "min_quantity": stock_items.c.min_stock,
        "is_active": stock_items.c.status,
    },
    # Optimistic concurrency: the UPDATE checks that stock is still what was read. If two people change the
    # quantity at once, the second gets StaleDataError, which the API turns into 409 (IsConcurrencyToken in EF).
    version_id_col=stock_items.c.stock,
    version_id_generator=False,
)
mapper(
    StockMovement,
    stock_log,
    properties={
        "id": stock_log.c.log_id,
        "created_at": stock_log.c.date_added,
        "item": relationship(StockItem, lazy="raise"),
    },
)
mapper(PurchaseOrderLine, purchase_order_lines)
mapper(
    PurchaseOrder,
    purchase_orders,
    properties={
        "_lines": relationship(
            PurchaseOrderLine, order_by=purchase_order_lines.c.id, cascade="all, delete-orphan", lazy="selectin"
        )
    },
)
# Two moves from one warehouse at once cannot both pass.
mapper(StockBalance, stock_locations, version_id_col=stock_locations.c.quantity, version_id_generator=False)
mapper(StockTransfer, stock_transfers)
