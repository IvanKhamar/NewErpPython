import uuid
from datetime import datetime

from sqlalchemy import Select, delete, exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.auth.models import NewUser, UserDto, UserLoginRecord
from newerp.application.common import ukrainian_key
from newerp.infrastructure.models.users import DdUser, ErpUserLogin


def new_stamp() -> str:
    return uuid.uuid4().hex


class SqlUserLoginStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_username(self, username: str) -> UserLoginRecord | None:
        return await self._record(DdUser.username == username)

    async def find_by_id(self, user_id: int) -> UserLoginRecord | None:
        return await self._record(DdUser.user_id == user_id)

    async def set_last_login(self, user_id: int, logged_in_at: datetime) -> None:
        await self._session.execute(
            update(ErpUserLogin).where(ErpUserLogin.user_id == user_id).values(last_login_at=logged_in_at)
        )
        await self._session.commit()

    async def get_active_security_stamp(self, user_id: int) -> str | None:
        return await self._session.scalar(
            select(ErpUserLogin.security_stamp)
            .join(DdUser, DdUser.user_id == ErpUserLogin.user_id)
            .where(DdUser.user_id == user_id, DdUser.status.is_(True))
        )

    async def list_all(self) -> list[UserDto]:
        """Those with NewErp access first, then by name."""
        users = [self._user_dto(u, login) for u, login in await self._session.execute(self._users())]
        return sorted(users, key=lambda u: (not u.has_access, ukrainian_key(u.full_name)))

    async def get(self, user_id: int) -> UserDto | None:
        row = (await self._session.execute(self._users().where(DdUser.user_id == user_id))).first()
        return None if row is None else self._user_dto(*row)

    async def username_taken(self, username: str) -> bool:
        return bool(await self._session.scalar(select(exists().where(DdUser.username == username))))

    async def create_user(self, user: NewUser, now: datetime) -> int:
        # Empty password and salt match no password on the old site, so the person cannot log in to the old admin.
        # user_group_id = 0: no rights in the old admin.
        row = DdUser(
            user_group_id=0,
            username=user.username,
            password="",
            salt="",
            firstname=user.first_name,
            lastname=user.last_name,
            email=user.email,
            image="",
            code="",
            ip="",
            status=True,
            date_added=now,
        )
        self._session.add(row)
        await self._session.commit()
        return row.user_id

    async def save_login(
        self, user_id: int, role: str | None, password_hash: str | None, must_change_password: bool | None
    ) -> str:
        login = await self._session.get(ErpUserLogin, user_id)
        if login is None:
            if role is None or password_hash is None:
                raise ValueError("Для нового входу потрібні роль і пароль.")
            login = ErpUserLogin(user_id=user_id, must_change_password=False)
            self._session.add(login)

        if role is not None:
            login.role = role
        if password_hash is not None:
            login.password_hash = password_hash
        if must_change_password is not None:
            login.must_change_password = must_change_password
        login.security_stamp = new_stamp()
        await self._session.commit()
        return login.security_stamp

    async def delete_login(self, user_id: int) -> bool:
        result = await self._session.execute(delete(ErpUserLogin).where(ErpUserLogin.user_id == user_id))
        await self._session.commit()
        return result.rowcount > 0

    async def _record(self, condition) -> UserLoginRecord | None:
        row = (
            await self._session.execute(
                select(DdUser, ErpUserLogin).join(ErpUserLogin, ErpUserLogin.user_id == DdUser.user_id).where(condition)
            )
        ).first()
        if row is None:
            return None
        user, login = row
        return UserLoginRecord(
            user_id=user.user_id,
            username=user.username,
            full_name=f"{user.firstname} {user.lastname}",
            is_active=user.status,
            password_hash=login.password_hash,
            role=login.role,
            security_stamp=login.security_stamp,
            must_change_password=login.must_change_password,
        )

    @staticmethod
    def _users() -> Select[tuple[DdUser, ErpUserLogin]]:
        return select(DdUser, ErpUserLogin).outerjoin(ErpUserLogin, ErpUserLogin.user_id == DdUser.user_id)

    @staticmethod
    def _user_dto(user: DdUser, login: ErpUserLogin | None) -> UserDto:
        return UserDto(
            user_id=user.user_id,
            username=user.username,
            full_name=f"{user.firstname} {user.lastname}",
            email=user.email,
            active_on_old_site=user.status,
            has_access=login is not None,
            role=None if login is None else login.role,
            last_login_at=None if login is None else login.last_login_at,
        )
