"""OpenCart customers and returns: dd_customer with groups, addresses and notes; dd_return with its lookups and log.

dd_customer also holds the password hash, salt, token and cart: they are not mapped, so no query can read them.
"""

from datetime import date, datetime

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from newerp.infrastructure.db import Base


class DdCustomer(Base):
    __tablename__ = "dd_customer"

    customer_id: Mapped[int] = mapped_column(primary_key=True)
    customer_group_id: Mapped[int]
    firstname: Mapped[str] = mapped_column(String(32))
    lastname: Mapped[str] = mapped_column(String(32))
    email: Mapped[str] = mapped_column(String(96))
    telephone: Mapped[str] = mapped_column(String(32))
    newsletter: Mapped[bool] = mapped_column(default=False)
    address_id: Mapped[int] = mapped_column(default=0)  # the default address
    status: Mapped[bool]
    date_added: Mapped[datetime]


class DdCustomerGroupDescription(Base):
    __tablename__ = "dd_customer_group_description"

    customer_group_id: Mapped[int] = mapped_column(primary_key=True)
    language_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(32))


class DdAddress(Base):
    __tablename__ = "dd_address"

    address_id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int]
    firstname: Mapped[str] = mapped_column(String(32))
    lastname: Mapped[str] = mapped_column(String(32))
    address_1: Mapped[str] = mapped_column(String(128))
    address_2: Mapped[str] = mapped_column(String(128))
    city: Mapped[str] = mapped_column(String(128))


class DdCustomerHistory(Base):
    """A manager's note about a customer."""

    __tablename__ = "dd_customer_history"

    customer_history_id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int]
    comment: Mapped[str] = mapped_column(Text)
    date_added: Mapped[datetime]


class DdReturn(Base):
    __tablename__ = "dd_return"

    return_id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int]
    product_id: Mapped[int]
    customer_id: Mapped[int]
    firstname: Mapped[str] = mapped_column(String(32))
    lastname: Mapped[str] = mapped_column(String(32))
    email: Mapped[str] = mapped_column(String(96))
    telephone: Mapped[str] = mapped_column(String(32))
    product: Mapped[str] = mapped_column(String(255))
    model: Mapped[str] = mapped_column(String(64))
    quantity: Mapped[int]
    opened: Mapped[bool]
    return_reason_id: Mapped[int]
    return_action_id: Mapped[int]
    return_status_id: Mapped[int]
    comment: Mapped[str | None] = mapped_column(Text)
    # Deferred: the real database may hold the zero date 0000-00-00.
    date_ordered: Mapped[date] = mapped_column(deferred=True)
    date_added: Mapped[datetime]
    date_modified: Mapped[datetime]


class DdReturnHistory(Base):
    __tablename__ = "dd_return_history"

    return_history_id: Mapped[int] = mapped_column(primary_key=True)
    return_id: Mapped[int]
    return_status_id: Mapped[int]
    notify: Mapped[bool] = mapped_column(default=False)
    comment: Mapped[str] = mapped_column(Text)
    date_added: Mapped[datetime]


class DdReturnStatus(Base):
    __tablename__ = "dd_return_status"

    return_status_id: Mapped[int] = mapped_column(primary_key=True)
    language_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(32))


class DdReturnReason(Base):
    __tablename__ = "dd_return_reason"

    return_reason_id: Mapped[int] = mapped_column(primary_key=True)
    language_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))


class DdReturnAction(Base):
    __tablename__ = "dd_return_action"

    return_action_id: Mapped[int] = mapped_column(primary_key=True)
    language_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
