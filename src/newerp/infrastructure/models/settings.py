"""OpenCart settings (dd_setting): key-value rows per store and module code."""

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from newerp.infrastructure.db import Base


class DdSetting(Base):
    __tablename__ = "dd_setting"

    setting_id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(default=0)
    code: Mapped[str] = mapped_column(String(128))
    key: Mapped[str] = mapped_column(String(128))
    value: Mapped[str] = mapped_column(Text)
    serialized: Mapped[bool] = mapped_column(default=False)
