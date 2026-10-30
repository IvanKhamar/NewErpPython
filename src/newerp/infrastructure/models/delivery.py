"""Tables of the old site for warehouses and pickup points (the Nova Poshta tables are in delivery/mapping.py)."""

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from newerp.infrastructure.db import Base


class DdWarehouse(Base):
    """A shop's warehouse or pickup point ("Каталог → Склади" of the old admin)."""

    __tablename__ = "dd_warehouse"

    warehouse_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text)
    contact: Mapped[str] = mapped_column(String(128), default="")
    telephone: Mapped[str] = mapped_column(String(255), default="")
    city: Mapped[str] = mapped_column(String(128), default="")
    address: Mapped[str] = mapped_column(String(255), default="")
    type: Mapped[str] = mapped_column(String(64), default="")
    image: Mapped[str] = mapped_column(String(255), default="")  # uploaded in the site's admin; NewErp keeps it
