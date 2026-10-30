"""OpenCart order tables (MyISAM: no transactions, so multi-step writes are not atomic) and erp_order_manager."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from newerp.infrastructure.db import Base

Money = Numeric(15, 4)


class DdOrder(Base):
    __tablename__ = "dd_order"

    order_id: Mapped[int] = mapped_column(primary_key=True)
    invoice_no: Mapped[int] = mapped_column(default=0)
    invoice_prefix: Mapped[str] = mapped_column(String(26), default="")
    store_id: Mapped[int] = mapped_column(default=0)
    store_name: Mapped[str] = mapped_column(String(64), default="")
    store_url: Mapped[str] = mapped_column(String(255), default="")
    customer_id: Mapped[int] = mapped_column(default=0)
    customer_group_id: Mapped[int] = mapped_column(default=0)
    firstname: Mapped[str] = mapped_column(String(32), default="")
    lastname: Mapped[str] = mapped_column(String(32), default="")
    email: Mapped[str] = mapped_column(String(96), default="")
    telephone: Mapped[str] = mapped_column(String(32), default="")
    fax: Mapped[str] = mapped_column(String(32), default="")
    custom_field: Mapped[str] = mapped_column(Text, default="")
    payment_firstname: Mapped[str] = mapped_column(String(32), default="")
    payment_lastname: Mapped[str] = mapped_column(String(32), default="")
    payment_company: Mapped[str] = mapped_column(String(60), default="")
    payment_address_1: Mapped[str] = mapped_column(String(128), default="")
    payment_address_2: Mapped[str] = mapped_column(String(128), default="")
    payment_city: Mapped[str] = mapped_column(String(128), default="")
    payment_postcode: Mapped[str] = mapped_column(String(10), default="")
    payment_country: Mapped[str] = mapped_column(String(128), default="")
    payment_country_id: Mapped[int] = mapped_column(default=0)
    payment_zone: Mapped[str] = mapped_column(String(128), default="")
    payment_zone_id: Mapped[int] = mapped_column(default=0)
    payment_address_format: Mapped[str] = mapped_column(Text, default="")
    payment_custom_field: Mapped[str] = mapped_column(Text, default="")
    payment_method: Mapped[str] = mapped_column(String(128), default="")
    payment_code: Mapped[str] = mapped_column(String(128), default="")
    shipping_firstname: Mapped[str] = mapped_column(String(32), default="")
    shipping_lastname: Mapped[str] = mapped_column(String(32), default="")
    shipping_company: Mapped[str] = mapped_column(String(40), default="")
    shipping_address_1: Mapped[str] = mapped_column(String(128), default="")
    shipping_address_2: Mapped[str] = mapped_column(String(128), default="")
    shipping_city: Mapped[str] = mapped_column(String(128), default="")
    shipping_postcode: Mapped[str] = mapped_column(String(10), default="")
    shipping_country: Mapped[str] = mapped_column(String(128), default="")
    shipping_country_id: Mapped[int] = mapped_column(default=0)
    shipping_zone: Mapped[str] = mapped_column(String(128), default="")
    shipping_zone_id: Mapped[int] = mapped_column(default=0)
    shipping_address_format: Mapped[str] = mapped_column(Text, default="")
    shipping_custom_field: Mapped[str] = mapped_column(Text, default="")
    shipping_method: Mapped[str] = mapped_column(String(128), default="")
    shipping_code: Mapped[str] = mapped_column(String(128), default="")
    comment: Mapped[str] = mapped_column(Text, default="")
    total: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    order_status_id: Mapped[int] = mapped_column(default=0)
    affiliate_id: Mapped[int] = mapped_column(default=0)
    commission: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    marketing_id: Mapped[int] = mapped_column(default=0)
    tracking: Mapped[str] = mapped_column(String(64), default="")
    language_id: Mapped[int] = mapped_column(default=0)
    currency_id: Mapped[int] = mapped_column(default=0)
    currency_code: Mapped[str] = mapped_column(String(3), default="")
    currency_value: Mapped[Decimal] = mapped_column(Numeric(15, 8), default=Decimal(1))
    ip: Mapped[str] = mapped_column(String(40), default="")
    forwarded_ip: Mapped[str] = mapped_column(String(40), default="")
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    accept_language: Mapped[str] = mapped_column(String(255), default="")
    date_added: Mapped[datetime]
    date_modified: Mapped[datetime]
    # Own columns of the old site (db/reconstructed.sql).
    order_source_id: Mapped[int] = mapped_column(default=0)
    date_remind: Mapped[datetime | None]
    prepayment: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    instagram: Mapped[str] = mapped_column(String(128), default="")
    birthday_day: Mapped[int] = mapped_column(default=0)
    birthday_month: Mapped[int] = mapped_column(default=0)
    shipping_house: Mapped[str] = mapped_column(String(32), default="")
    shipping_flat: Mapped[str] = mapped_column(String(32), default="")
    callback: Mapped[bool] = mapped_column(default=False)
    user_id: Mapped[int] = mapped_column(default=0)


class DdOrderProduct(Base):
    __tablename__ = "dd_order_product"

    order_product_id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int]
    product_id: Mapped[int]
    name: Mapped[str] = mapped_column(String(255))
    model: Mapped[str] = mapped_column(String(64))
    quantity: Mapped[int]
    price: Mapped[Decimal] = mapped_column(Money)
    total: Mapped[Decimal] = mapped_column(Money)
    tax: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    reward: Mapped[int] = mapped_column(default=0)
    crosssell: Mapped[bool] = mapped_column(default=False)


class DdOrderOption(Base):
    __tablename__ = "dd_order_option"

    order_option_id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int]
    order_product_id: Mapped[int]
    product_option_id: Mapped[int]
    product_option_value_id: Mapped[int] = mapped_column(default=0)
    name: Mapped[str] = mapped_column(String(255))
    value: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(32))


class DdOrderTotal(Base):
    __tablename__ = "dd_order_total"

    order_total_id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int]
    code: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(255))
    value: Mapped[Decimal] = mapped_column(Money)
    sort_order: Mapped[int]


class DdOrderStatus(Base):
    __tablename__ = "dd_order_status"

    order_status_id: Mapped[int] = mapped_column(primary_key=True)
    language_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(32))
    color: Mapped[str] = mapped_column(String(7), default="")


class DdOrderSource(Base):
    __tablename__ = "dd_order_source"

    order_source_id: Mapped[int] = mapped_column(primary_key=True)
    language_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))


class DdOrderStatusLog(Base):
    __tablename__ = "dd_order_status_log"

    order_status_log_id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int]
    user_id: Mapped[int]
    old_status_id: Mapped[int]
    new_status_id: Mapped[int]
    date_added: Mapped[datetime]


class ErpOrderManager(Base):
    """The manager responsible for an order (NewErp's own table)."""

    __tablename__ = "erp_order_manager"

    order_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    user_id: Mapped[int]
    assigned_by_user_id: Mapped[int]
    assigned_at: Mapped[datetime]
