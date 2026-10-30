"""Nova Poshta tables of the old site."""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from newerp.infrastructure.db import Base


class DdNovaposhtaDocument(Base):
    """A waybill (TTN) of an order."""

    __tablename__ = "dd_novaposhta_document"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int]
    number: Mapped[str] = mapped_column(String(32))
