"""Dependency wiring: which implementation each service gets (like AddScoped/AddSingleton in Program.cs).

FastAPI calls these functions per request; a function with `yield` (get_session) also cleans up afterwards.
"""

from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.auth.services import AuthService, UserAdminService
from newerp.application.auth.throttle import LoginThrottle, LoginThrottleOptions
from newerp.application.common import Clock, SystemClock
from newerp.infrastructure.auth.password_hasher import Pbkdf2PasswordHasher
from newerp.infrastructure.auth.user_login_store import SqlUserLoginStore
from newerp.infrastructure.db import get_session

Session = Annotated[AsyncSession, Depends(get_session)]


# ── Singletons: one object for the whole process ──


@lru_cache
def get_clock() -> Clock:
    return SystemClock()


@lru_cache
def get_hasher() -> Pbkdf2PasswordHasher:
    return Pbkdf2PasswordHasher()


@lru_cache
def get_login_throttle() -> LoginThrottle:
    return LoginThrottle(LoginThrottleOptions(), get_clock())


ClockDep = Annotated[Clock, Depends(get_clock)]
HasherDep = Annotated[Pbkdf2PasswordHasher, Depends(get_hasher)]


# ── Per request ──


def get_user_login_store(session: Session) -> SqlUserLoginStore:
    return SqlUserLoginStore(session)


UserLoginStoreDep = Annotated[SqlUserLoginStore, Depends(get_user_login_store)]


def get_auth_service(store: UserLoginStoreDep, hasher: HasherDep, clock: ClockDep) -> AuthService:
    return AuthService(store, hasher, clock)


def get_user_admin_service(store: UserLoginStoreDep, hasher: HasherDep, clock: ClockDep) -> UserAdminService:
    return UserAdminService(store, hasher, clock)
