from datetime import datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from newerp.infrastructure.db import Base


class DdUser(Base):
    """A user of the old site's admin."""

    __tablename__ = "dd_user"

    user_id: Mapped[int] = mapped_column(primary_key=True)
    user_group_id: Mapped[int]
    username: Mapped[str] = mapped_column(String(20))
    password: Mapped[str] = mapped_column(String(40))
    salt: Mapped[str] = mapped_column(String(9))
    firstname: Mapped[str] = mapped_column(String(32))
    lastname: Mapped[str] = mapped_column(String(32))
    email: Mapped[str] = mapped_column(String(96))
    image: Mapped[str] = mapped_column(String(255))
    code: Mapped[str] = mapped_column(String(40))
    ip: Mapped[str] = mapped_column(String(40))
    status: Mapped[bool]
    date_added: Mapped[datetime]


class ErpUserLogin(Base):
    """Password hash and role of a dd_user in NewErp."""

    __tablename__ = "erp_user_login"

    # The id comes from dd_user; the database does not generate it.
    user_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32))
    last_login_at: Mapped[datetime | None]
    # The account version. A new value makes every cookie issued before invalid.
    security_stamp: Mapped[str] = mapped_column(String(36))
    must_change_password: Mapped[bool] = mapped_column(default=False)
