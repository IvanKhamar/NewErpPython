"""Access to the old OpenCart admin that staff still use (Python first, items 64 and 65): its user groups with
per-page permissions, and the API users the storefront's order editing logs in with.

The ERP's own roles stay separate (erp_user_login). Two rules keep these from turning into a hole:
- the admin cannot take the permission pages away from their own OpenCart group (they would lock themselves out);
- an API key is made by the server, shown once and never again; OpenCart compares it as plain text, so it is
  stored the way OpenCart needs it, but the ERP never reads it back out."""

import json
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from pydantic import Field

from newerp.application.common import Clock, ConflictError, Dto, NotFoundError
from newerp.domain.common import DomainError

audit = logging.getLogger("newerp.audit")
_ROUTE = re.compile(r"[a-z0-9_]+(?:/[a-z0-9_]+){1,2}")
_IP = re.compile(r"[0-9a-fA-F:.]{2,40}")
PERMISSION_PAGE = "user/user_permission"  # the page that edits these groups
API_KEY_LENGTH = 256  # OpenCart makes 256-character keys


# ── 64: user groups ──


class UserGroupDto(Dto):
    id: int = 0
    name: str = Field(default="", max_length=64)
    access: list[str] = Field(default_factory=list, max_length=500)  # routes the group may open
    modify: list[str] = Field(default_factory=list, max_length=500)  # routes the group may change
    users: int = 0


class UserGroupStore(Protocol):
    async def groups(self) -> list[UserGroupDto]:
        """With their permissions decoded and the number of their users."""
        ...

    async def save(self, group: UserGroupDto) -> int: ...

    async def delete(self, group_id: int) -> None: ...

    async def group_of_user(self, user_id: int) -> int | None:
        """The OpenCart group of the ERP user (dd_user.user_group_id)."""
        ...


def decode_permission(text: str) -> tuple[list[str], list[str]]:
    """OpenCart keeps {"access": [...], "modify": [...]} as JSON; a broken value reads as no permissions."""
    try:
        data = json.loads(text or "{}")
    except ValueError:
        return [], []
    if not isinstance(data, dict):
        return [], []
    return [str(r) for r in data.get("access") or []], [str(r) for r in data.get("modify") or []]


class UserGroupService:
    def __init__(self, store: UserGroupStore) -> None:
        self._store = store

    async def groups(self) -> list[UserGroupDto]:
        return await self._store.groups()

    async def known_routes(self) -> list[str]:
        """Every page any group mentions: what the form offers to tick."""
        routes = {r for g in await self._store.groups() for r in (*g.access, *g.modify)}
        return sorted(routes)

    async def save(self, group_id: int | None, group: UserGroupDto, user_id: int) -> UserGroupDto:
        groups = {g.id: g for g in await self._store.groups()}
        if group_id is not None and group_id not in groups:
            raise NotFoundError(f"Групи {group_id} немає.")
        name = group.name.strip()
        if not name:
            raise DomainError("Вкажіть назву групи.")
        access, modify = _routes(group.access), _routes(group.modify)
        own = await self._store.group_of_user(user_id)
        if (
            group_id is not None
            and group_id == own
            and (PERMISSION_PAGE not in access or PERMISSION_PAGE not in modify)
        ):
            raise DomainError(f"Не можна забрати у своєї групи «{PERMISSION_PAGE}»: ви втратите доступ до груп.")
        clean = group.model_copy(update={"id": group_id or 0, "name": name, "access": access, "modify": modify})
        saved = await self._store.save(clean)
        audit.info("opencart user group saved: %s, by user %s", saved, user_id)
        return next(g for g in await self._store.groups() if g.id == saved)

    async def delete(self, group_id: int, user_id: int) -> None:
        group = next((g for g in await self._store.groups() if g.id == group_id), None)
        if group is None:
            raise NotFoundError(f"Групи {group_id} немає.")
        if group.users:
            raise DomainError(f"У групі {group.users} користувачів: спершу перенесіть їх.")
        await self._store.delete(group_id)
        audit.info("opencart user group deleted: %s, by user %s", group_id, user_id)


def _routes(routes: list[str]) -> list[str]:
    clean = [r.strip() for r in routes if r.strip()]
    bad = [r for r in clean if not _ROUTE.fullmatch(r)]
    if bad:
        raise DomainError(f"Сторінки адмінки пишуться як catalog/product: {', '.join(bad[:5])}.")
    return sorted(set(clean))


# ── 65: API users ──


class ApiUserDto(Dto):
    """The key is never part of it: has_key only says whether one is set."""

    id: int = 0
    username: str = Field(default="", max_length=64)
    enabled: bool = True
    ips: list[str] = Field(default_factory=list, max_length=50)
    has_key: bool = False
    sessions: int = 0
    date_modified: datetime | None = None


class ApiKeyDto(Dto):
    """Shown once, right after it is made."""

    api_id: int
    key: str


@dataclass(frozen=True)
class ApiUserRow:
    id: int
    username: str
    enabled: bool
    ips: list[str]
    has_key: bool
    sessions: int
    date_modified: datetime | None


class ApiUserStore(Protocol):
    async def users(self) -> list[ApiUserRow]: ...

    async def username_owner(self, username: str) -> int | None: ...

    async def save(self, user: ApiUserDto, now: datetime) -> int:
        """The user and its IP list; the key is not touched."""
        ...

    async def set_key(self, api_id: int, key: str, now: datetime) -> None:
        """Also ends the user's sessions, so the old key stops working at once."""
        ...

    async def delete(self, api_id: int) -> None: ...


class ApiUserService:
    def __init__(self, store: ApiUserStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    async def users(self) -> list[ApiUserDto]:
        return [ApiUserDto(**row.__dict__) for row in await self._store.users()]

    async def save(self, api_id: int | None, user: ApiUserDto, user_id: int) -> ApiUserDto:
        if api_id is not None and api_id not in {u.id for u in await self._store.users()}:
            raise NotFoundError(f"Користувача API {api_id} немає.")
        username = user.username.strip()
        if not username:
            raise DomainError("Вкажіть ім'я користувача API.")
        owner = await self._store.username_owner(username)
        if owner is not None and owner != api_id:
            raise ConflictError(f"Ім'я «{username}» вже має користувач API №{owner}.")
        ips = sorted({ip.strip() for ip in user.ips if ip.strip()})
        bad = [ip for ip in ips if not _IP.fullmatch(ip)]
        if bad:
            raise DomainError(f"Не IP-адреси: {', '.join(bad)}.")
        saved = await self._store.save(
            user.model_copy(update={"id": api_id or 0, "username": username, "ips": ips}), self._now()
        )
        audit.info("api user saved: %s, by user %s", saved, user_id)
        return next(u for u in await self.users() if u.id == saved)

    async def new_key(self, api_id: int, user_id: int) -> ApiKeyDto:
        """A random key in place of the old one (which stops working); the only time the key is shown."""
        if api_id not in {u.id for u in await self._store.users()}:
            raise NotFoundError(f"Користувача API {api_id} немає.")
        key = secrets.token_urlsafe(API_KEY_LENGTH)[:API_KEY_LENGTH]
        await self._store.set_key(api_id, key, self._now())
        audit.warning("api key replaced: %s, by user %s", api_id, user_id)
        return ApiKeyDto(api_id=api_id, key=key)

    async def delete(self, api_id: int, user_id: int) -> None:
        if api_id not in {u.id for u in await self._store.users()}:
            raise NotFoundError(f"Користувача API {api_id} немає.")
        await self._store.delete(api_id)
        audit.info("api user deleted: %s, by user %s", api_id, user_id)

    def _now(self) -> datetime:
        return self._clock.now().replace(microsecond=0)
