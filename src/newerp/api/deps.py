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
from newerp.application.orders.ports import OrderStatusHandler
from newerp.application.orders.services import NewOrderService, OrderService
from newerp.application.production.services import OrderSewnStockHandler
from newerp.config import get_settings
from newerp.infrastructure.auth.password_hasher import Pbkdf2PasswordHasher
from newerp.infrastructure.auth.user_login_store import SqlUserLoginStore
from newerp.infrastructure.db import get_session
from newerp.infrastructure.orders.new_order_store import SqlNewOrderStore
from newerp.infrastructure.orders.queries import SqlOrderQueries
from newerp.infrastructure.orders.store import SqlOrderStore
from newerp.infrastructure.production.repositories import SqlProductionRepository, SqlStorefrontStock

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


# ── Orders ──


def get_order_queries(session: Session) -> SqlOrderQueries:
    return SqlOrderQueries(session, get_settings().language_id)


def get_order_store(session: Session) -> SqlOrderStore:
    return SqlOrderStore(session)


OrderStoreDep = Annotated[SqlOrderStore, Depends(get_order_store)]


def get_order_status_handlers(session: Session, clock: ClockDep) -> list[OrderStatusHandler]:
    """What other modules do on a status change (IOrderStatusHandler implementations registered in .NET DI)."""
    settings = get_settings()
    production = SqlProductionRepository(session, settings.language_id)
    storefront = SqlStorefrontStock(session, settings.language_id)
    return [
        OrderSewnStockHandler(production, storefront, settings.production.order_subtract_status_ids, clock),
    ]


def get_order_service(
    store: OrderStoreDep,
    clock: ClockDep,
    handlers: Annotated[list[OrderStatusHandler], Depends(get_order_status_handlers)],
) -> OrderService:
    return OrderService(store, clock, handlers)


def get_new_order_service(
    session: Session,
    orders: OrderStoreDep,
    order_service: Annotated[OrderService, Depends(get_order_service)],
    clock: ClockDep,
) -> NewOrderService:
    return NewOrderService(SqlNewOrderStore(session, get_settings().language_id), orders, order_service, clock)
