"""Tracking codes (dd_marketing) with their orders, and the voucher themes."""

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Text, delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.marketing.tracking import TrackingCodeDto
from newerp.application.marketing.voucher_admin import VoucherThemeDto
from newerp.infrastructure.models.marketing import DdVoucher
from newerp.infrastructure.models.orders import DdOrder

metadata = MetaData()
marketing = Table(
    "dd_marketing",
    metadata,
    Column("marketing_id", Integer, primary_key=True),
    Column("name", String(32)),
    Column("description", Text),
    Column("code", String(64)),
    Column("clicks", Integer),
    Column("date_added", DateTime),
)
voucher_theme = Table(
    "dd_voucher_theme", metadata, Column("voucher_theme_id", Integer, primary_key=True), Column("image", String(255))
)
voucher_theme_description = Table(
    "dd_voucher_theme_description",
    metadata,
    Column("voucher_theme_id", Integer, primary_key=True),
    Column("language_id", Integer, primary_key=True),
    Column("name", String(32)),
)


class SqlTrackingStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def codes(self) -> list[TrackingCodeDto]:
        m, o = marketing.c, DdOrder
        placed = (o.marketing_id == m.marketing_id) & (o.order_status_id > 0)
        orders = select(func.count()).where(placed).scalar_subquery()
        revenue = select(func.coalesce(func.sum(o.total), 0)).where(placed).scalar_subquery()
        rows = await self._session.execute(
            select(m.marketing_id, m.name, m.description, m.code, m.clicks, orders, revenue, m.date_added).order_by(
                m.date_added.desc()
            )
        )
        return [
            TrackingCodeDto(
                id=r[0],
                name=r[1],
                description=r[2] or "",
                code=r[3],
                clicks=r[4],
                orders=r[5],
                revenue=r[6],
                date_added=r[7],
            )
            for r in rows
        ]

    async def code_owner(self, code: str) -> int | None:
        return await self._session.scalar(select(marketing.c.marketing_id).where(marketing.c.code == code).limit(1))

    async def save(self, item: TrackingCodeDto, now: datetime) -> int:
        values = {"name": item.name, "description": item.description, "code": item.code}
        if item.id:
            await self._session.execute(update(marketing).where(marketing.c.marketing_id == item.id).values(values))
            marketing_id = item.id
        else:
            query = insert(marketing).values({**values, "clicks": 0, "date_added": now})
            marketing_id = (await self._session.execute(query)).inserted_primary_key[0]
        await self._session.commit()
        return marketing_id

    async def delete(self, marketing_id: int) -> bool:
        result = await self._session.execute(delete(marketing).where(marketing.c.marketing_id == marketing_id))
        await self._session.commit()
        return result.rowcount > 0


class SqlVoucherThemeStore:
    def __init__(self, session: AsyncSession, language_id: int) -> None:
        self._session = session
        self._language_id = language_id

    async def themes(self) -> list[VoucherThemeDto]:
        t, d = voucher_theme.c, voucher_theme_description.c
        used = select(func.count()).where(DdVoucher.voucher_theme_id == t.voucher_theme_id).scalar_subquery()
        rows = await self._session.execute(
            select(t.voucher_theme_id, d.name, t.image, used)
            .outerjoin(
                voucher_theme_description,
                (d.voucher_theme_id == t.voucher_theme_id) & (d.language_id == self._language_id),
            )
            .order_by(d.name)
        )
        return [VoucherThemeDto(id=r[0], name=r[1] or "", image=r[2] or "", vouchers=r[3]) for r in rows]

    async def save(self, theme: VoucherThemeDto) -> int:
        s, t = self._session, voucher_theme.c
        if theme.id:
            await s.execute(update(voucher_theme).where(t.voucher_theme_id == theme.id).values(image=theme.image))
            theme_id = theme.id
        else:
            theme_id = (await s.execute(insert(voucher_theme).values(image=theme.image))).inserted_primary_key[0]
        d = voucher_theme_description.c
        await s.execute(
            delete(voucher_theme_description).where(d.voucher_theme_id == theme_id, d.language_id == self._language_id)
        )
        await s.execute(
            insert(voucher_theme_description).values(
                voucher_theme_id=theme_id, language_id=self._language_id, name=theme.name
            )
        )
        await s.commit()
        return theme_id

    async def delete(self, theme_id: int) -> None:
        for table in (voucher_theme_description, voucher_theme):
            await self._session.execute(delete(table).where(table.c.voucher_theme_id == theme_id))
        await self._session.commit()
