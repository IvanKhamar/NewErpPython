"""OpenCart catalog tables: products, their names and options (sizes)."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from newerp.infrastructure.db import Base
from newerp.infrastructure.models.orders import Money


class DdProduct(Base):
    __tablename__ = "dd_product"

    product_id: Mapped[int] = mapped_column(primary_key=True)
    model: Mapped[str] = mapped_column(String(64))
    sku: Mapped[str] = mapped_column(String(64), default="")
    upc: Mapped[str] = mapped_column(String(12), default="")
    ean: Mapped[str] = mapped_column(String(14), default="")
    jan: Mapped[str] = mapped_column(String(13), default="")
    isbn: Mapped[str] = mapped_column(String(17), default="")
    mpn: Mapped[str] = mapped_column(String(64), default="")
    location: Mapped[str] = mapped_column(String(128), default="")
    quantity: Mapped[int] = mapped_column(default=0)
    stock_status_id: Mapped[int] = mapped_column(default=0)
    image: Mapped[str | None] = mapped_column(String(255))
    manufacturer_id: Mapped[int] = mapped_column(default=0)
    shipping: Mapped[bool] = mapped_column(default=True)
    price: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    points: Mapped[int] = mapped_column(default=0)
    tax_class_id: Mapped[int] = mapped_column(default=0)
    # The real database may hold '0000-00-00' here, which no date type can read: select it only as text.
    date_available: Mapped[date] = mapped_column(deferred=True)
    weight: Mapped[Decimal] = mapped_column(Numeric(15, 8), default=Decimal(0))
    weight_class_id: Mapped[int] = mapped_column(default=0)
    subtract: Mapped[bool] = mapped_column(default=True)
    minimum: Mapped[int] = mapped_column(default=1)
    sort_order: Mapped[int] = mapped_column(default=0)
    status: Mapped[bool] = mapped_column(default=False)
    viewed: Mapped[int] = mapped_column(default=0)
    date_added: Mapped[datetime]
    date_modified: Mapped[datetime]


class DdProductDescription(Base):
    __tablename__ = "dd_product_description"

    product_id: Mapped[int] = mapped_column(primary_key=True)
    language_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    tag: Mapped[str] = mapped_column(Text, default="")
    meta_title: Mapped[str] = mapped_column(String(255), default="")
    meta_description: Mapped[str] = mapped_column(String(255), default="")
    meta_keyword: Mapped[str] = mapped_column(String(255), default="")


class DdProductOptionValue(Base):
    """A product's option value (usually a size) with its stock and price."""

    __tablename__ = "dd_product_option_value"

    product_option_value_id: Mapped[int] = mapped_column(primary_key=True)
    product_option_id: Mapped[int]
    product_id: Mapped[int]
    option_id: Mapped[int]
    option_value_id: Mapped[int]
    quantity: Mapped[int] = mapped_column(default=0)
    subtract: Mapped[bool] = mapped_column(default=False)
    price: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    price_prefix: Mapped[str] = mapped_column(String(1), default="+")
    points: Mapped[int] = mapped_column(default=0)
    points_prefix: Mapped[str] = mapped_column(String(1), default="+")
    weight: Mapped[Decimal] = mapped_column(Numeric(15, 8), default=Decimal(0))
    weight_prefix: Mapped[str] = mapped_column(String(1), default="+")
    pre_order_quantity: Mapped[int] = mapped_column(default=0)


class DdOptionDescription(Base):
    __tablename__ = "dd_option_description"

    option_id: Mapped[int] = mapped_column(primary_key=True)
    language_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))


class DdOptionValueDescription(Base):
    __tablename__ = "dd_option_value_description"

    option_value_id: Mapped[int] = mapped_column(primary_key=True)
    language_id: Mapped[int] = mapped_column(primary_key=True)
    option_id: Mapped[int]
    name: Mapped[str] = mapped_column(String(128))


class DdProductToCategory(Base):
    __tablename__ = "dd_product_to_category"

    product_id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(primary_key=True)


class DdCategory(Base):
    __tablename__ = "dd_category"

    category_id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int] = mapped_column(default=0)
    sort_order: Mapped[int] = mapped_column(default=0)
    status: Mapped[bool]
    # How many days from the order date a product of this category must be ready (0: not set).
    production_period: Mapped[int] = mapped_column(default=0)
