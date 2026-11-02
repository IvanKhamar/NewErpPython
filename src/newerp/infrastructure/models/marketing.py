"""OpenCart marketing tables: coupons, gift vouchers, product questions and newsletter subscribers."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from newerp.infrastructure.db import Base
from newerp.infrastructure.models.orders import Money


class DdCoupon(Base):
    __tablename__ = "dd_coupon"

    coupon_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    code: Mapped[str] = mapped_column(String(20))
    type: Mapped[str] = mapped_column(String(1))  # "P": percent, "F": fixed sum
    discount: Mapped[Decimal] = mapped_column(Money)
    logged: Mapped[bool]
    shipping: Mapped[bool]
    total: Mapped[Decimal] = mapped_column(Money)  # minimal order sum
    date_start: Mapped[date]
    date_end: Mapped[date]
    uses_total: Mapped[int]
    uses_customer: Mapped[str] = mapped_column(String(11))
    status: Mapped[bool]
    date_added: Mapped[datetime]


class DdVoucher(Base):
    __tablename__ = "dd_voucher"

    voucher_id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int]  # the order the voucher was bought in; 0: made in the admin
    code: Mapped[str] = mapped_column(String(10))
    from_name: Mapped[str] = mapped_column(String(64))
    from_email: Mapped[str] = mapped_column(String(96))
    to_name: Mapped[str] = mapped_column(String(64))
    to_email: Mapped[str] = mapped_column(String(96))
    voucher_theme_id: Mapped[int]
    message: Mapped[str] = mapped_column(Text)
    amount: Mapped[Decimal] = mapped_column(Money)
    status: Mapped[bool]
    date_added: Mapped[datetime]


class DdVoucherHistory(Base):
    """A spending (negative amount) of a voucher in an order."""

    __tablename__ = "dd_voucher_history"

    voucher_history_id: Mapped[int] = mapped_column(primary_key=True)
    voucher_id: Mapped[int]
    order_id: Mapped[int]
    amount: Mapped[Decimal] = mapped_column(Money)
    date_added: Mapped[datetime]


class DdVoucherTheme(Base):
    __tablename__ = "dd_voucher_theme"

    voucher_theme_id: Mapped[int] = mapped_column(primary_key=True)
    image: Mapped[str] = mapped_column(String(255))


class DdVoucherThemeDescription(Base):
    __tablename__ = "dd_voucher_theme_description"

    voucher_theme_id: Mapped[int] = mapped_column(primary_key=True)
    language_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(32))


class DdQuestion(Base):
    """A question about a product from the shop ("Є розмір M?"), with the asker's phone."""

    __tablename__ = "dd_question"

    question_id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(default=0)
    name: Mapped[str] = mapped_column(String(64), default="")
    telephone: Mapped[str] = mapped_column(String(32), default="")
    question: Mapped[str] = mapped_column(Text)
    date_added: Mapped[datetime]


class DdSubscribe(Base):
    __tablename__ = "dd_subscribe"

    subscribe_id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(96))
    date_added: Mapped[datetime]


class DdCouponProduct(Base):
    """A product a coupon works on (none: the whole cart)."""

    __tablename__ = "dd_coupon_product"

    coupon_product_id: Mapped[int] = mapped_column(primary_key=True)
    coupon_id: Mapped[int]
    product_id: Mapped[int]


class DdCouponCategory(Base):
    __tablename__ = "dd_coupon_category"

    coupon_id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(primary_key=True)


class DdCouponProductExclude(Base):
    """The old site's own table: a product the coupon does not work on."""

    __tablename__ = "dd_coupon_product_exclude"

    coupon_id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(primary_key=True)


class DdCouponCategoryExclude(Base):
    __tablename__ = "dd_coupon_category_exclude"

    coupon_id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(primary_key=True)


class DdCouponHistory(Base):
    """A use of a coupon in an order (the storefront writes it)."""

    __tablename__ = "dd_coupon_history"

    coupon_history_id: Mapped[int] = mapped_column(primary_key=True)
    coupon_id: Mapped[int]
    order_id: Mapped[int]
    customer_id: Mapped[int]
    amount: Mapped[Decimal] = mapped_column(Money)
    date_added: Mapped[datetime]
