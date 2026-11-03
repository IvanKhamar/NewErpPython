"""OpenCart user groups (dd_user_group) and API users (dd_api, dd_api_ip, dd_api_session)."""

import json
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, String, Table, Text, delete, func, insert, select
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.opencart_access import ApiUserDto, ApiUserRow, UserGroupDto, decode_permission
from newerp.infrastructure.models.users import DdUser

metadata = MetaData()
user_group = Table(
    "dd_user_group",
    metadata,
    Column("user_group_id", Integer, primary_key=True),
    Column("name", String(64)),
    Column("permission", Text),
)
api = Table(
    "dd_api",
    metadata,
    Column("api_id", Integer, primary_key=True),
    Column("username", String(64)),
    Column("key", Text),
    Column("status", Boolean),
    Column("date_added", DateTime),
    Column("date_modified", DateTime),
)
api_ip = Table(
    "dd_api_ip", metadata, Column("api_ip_id", Integer, primary_key=True), Column("api_id", Integer),
    Column("ip", String(40)),
)  # fmt: skip
api_session = Table(
    "dd_api_session", metadata, Column("api_session_id", Integer, primary_key=True), Column("api_id", Integer),
    Column("session_id", String(32)), Column("ip", String(40)), Column("date_added", DateTime),
    Column("date_modified", DateTime),
)  # fmt: skip


class SqlUserGroupStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def groups(self) -> list[UserGroupDto]:
        g = user_group.c
        users = select(func.count()).where(DdUser.user_group_id == g.user_group_id).scalar_subquery()
        rows = await self._session.execute(select(g.user_group_id, g.name, g.permission, users).order_by(g.name))
        result = []
        for group_id, name, permission, count in rows:
            access, modify = decode_permission(permission)
            result.append(UserGroupDto(id=group_id, name=name, access=access, modify=modify, users=count))
        return result

    async def save(self, group: UserGroupDto) -> int:
        values = {"name": group.name, "permission": json.dumps({"access": group.access, "modify": group.modify})}
        if group.id:
            await self._session.execute(
                sql_update(user_group).where(user_group.c.user_group_id == group.id).values(values)
            )
            group_id = group.id
        else:
            group_id = (await self._session.execute(insert(user_group).values(values))).inserted_primary_key[0]
        await self._session.commit()
        return group_id

    async def delete(self, group_id: int) -> None:
        await self._session.execute(delete(user_group).where(user_group.c.user_group_id == group_id))
        await self._session.commit()

    async def group_of_user(self, user_id: int) -> int | None:
        return await self._session.scalar(select(DdUser.user_group_id).where(DdUser.user_id == user_id))


class SqlApiUserStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def users(self) -> list[ApiUserRow]:
        a = api.c
        sessions = select(func.count()).where(api_session.c.api_id == a.api_id).scalar_subquery()
        rows = (
            await self._session.execute(
                # Only whether a key is set: the key itself never leaves the database through the ERP.
                select(a.api_id, a.username, a.status, func.length(a.key) > 0, sessions, a.date_modified).order_by(
                    a.username
                )
            )
        ).all()
        ips: dict[int, list[str]] = {}
        for api_id, ip in await self._session.execute(select(api_ip.c.api_id, api_ip.c.ip).order_by(api_ip.c.ip)):
            ips.setdefault(api_id, []).append(ip)
        return [
            ApiUserRow(
                id=r[0],
                username=r[1],
                enabled=bool(r[2]),
                ips=ips.get(r[0], []),
                has_key=bool(r[3]),
                sessions=r[4],
                date_modified=r[5],
            )  # fmt: skip
            for r in rows
        ]

    async def username_owner(self, username: str) -> int | None:
        return await self._session.scalar(select(api.c.api_id).where(api.c.username == username).limit(1))

    async def save(self, user: ApiUserDto, now: datetime) -> int:
        s = self._session
        values = {"username": user.username, "status": user.enabled, "date_modified": now}
        if user.id:
            await s.execute(sql_update(api).where(api.c.api_id == user.id).values(values))
            api_id = user.id
        else:
            api_id = (
                await s.execute(insert(api).values({**values, "key": "", "date_added": now}))
            ).inserted_primary_key[0]
        await s.execute(delete(api_ip).where(api_ip.c.api_id == api_id))
        if user.ips:
            await s.execute(insert(api_ip), [{"api_id": api_id, "ip": ip} for ip in user.ips])
        if not user.enabled:
            await s.execute(delete(api_session).where(api_session.c.api_id == api_id))
        await s.commit()
        return api_id

    async def set_key(self, api_id: int, key: str, now: datetime) -> None:
        await self._session.execute(sql_update(api).where(api.c.api_id == api_id).values(key=key, date_modified=now))
        await self._session.execute(delete(api_session).where(api_session.c.api_id == api_id))
        await self._session.commit()

    async def delete(self, api_id: int) -> None:
        for table in (api_session, api_ip, api):
            await self._session.execute(delete(table).where(table.c.api_id == api_id))
        await self._session.commit()
