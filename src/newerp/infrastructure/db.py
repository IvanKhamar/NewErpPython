"""Async SQLAlchemy engine and session (like DbContext registered in DI)."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from newerp.config import get_settings

# The engine owns the connection pool; it connects lazily, on the first query.
engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
# No autoflush: like EF, nothing is written before commit (the OpenCart tables are MyISAM and cannot roll back).
session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


class Base(DeclarativeBase):
    """Base class for ORM models. Database-first: models must match db/*.sql, never create tables from code."""


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, closed when the request ends."""
    async with session_factory() as session:
        yield session
