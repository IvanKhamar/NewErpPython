"""Maps the delivery domain classes to the Nova Poshta tables of the old site and our erp_shipment_sender."""

from sqlalchemy import Boolean, Column, Date, DateTime, Integer, Numeric, String, Table

from newerp.domain.delivery import NovaPoshtaWarehouse, ShipmentDocument
from newerp.infrastructure.db import Base

metadata = Base.metadata

shipment_documents = Table(
    "dd_novaposhta_document",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("order_id", Integer),
    Column("carrier", String(32)),
    Column("ref", String(64)),
    Column("number", String(32)),
    Column("cost", Numeric(15, 4)),
    Column("status", String(255)),
    Column("status_code", Integer),
    Column("recipient_address", String(255)),
    Column("redelivery", Boolean),
    Column("date_created", DateTime, nullable=True),
    Column("date_scheduled", Date, nullable=True),
    Column("date_payed_keeping", Date, nullable=True),
)

nova_poshta_warehouses = Table(
    "dd_novaposhta_warehouse",
    metadata,
    Column("ref", String(36), primary_key=True),
    Column("description", String(255)),
    Column("category_of_warehouse", String(32)),
    Column("city_ref", String(36)),
    Column("city_description", String(255)),
    Column("weight", Integer),
    Column("sort_order", Integer),
    Column("status", Boolean),
)

# Which sender (and so which API key) made a waybill in NewErp; waybills of the old site have no row here.
shipment_senders = Table(
    "erp_shipment_sender",
    metadata,
    Column("shipment_id", Integer, primary_key=True, autoincrement=False),
    Column("sender_id", String(32)),
    Column("created_by_user_id", Integer),
    Column("created_at", DateTime),
)

Base.registry.map_imperatively(ShipmentDocument, shipment_documents)
Base.registry.map_imperatively(
    NovaPoshtaWarehouse,
    nova_poshta_warehouses,
    properties={
        "category": nova_poshta_warehouses.c.category_of_warehouse,
        "max_weight": nova_poshta_warehouses.c.weight,
    },
)
