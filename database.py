"""Async SQLAlchemy engine/session management."""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from config import get_settings, normalize_database_url

log = logging.getLogger(__name__)

engine: AsyncEngine | None = None
SessionFactory: async_sessionmaker[AsyncSession] | None = None


def init_engine(url: str | None = None, ssl: bool | None = None) -> AsyncEngine:
    """Create the global engine. ``url`` may be any Railway/SQLAlchemy URL."""
    global engine, SessionFactory
    settings = get_settings()
    if url is None:
        url, ssl_required = settings.database_url, settings.database_ssl
    else:
        url, ssl_required = normalize_database_url(url)
    if ssl is not None:
        ssl_required = ssl
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("postgresql+asyncpg://"):
        kwargs.update(pool_size=10, max_overflow=10)
        if ssl_required:
            kwargs["connect_args"] = {"ssl": True}
    engine = create_async_engine(url, **kwargs)
    SessionFactory = async_sessionmaker(engine, expire_on_commit=False)
    return engine


def transaction():
    """``async with transaction() as session:`` -> commits on success, rolls back on error."""
    if SessionFactory is None:
        raise RuntimeError("Database not initialised; call init_engine() first")
    return SessionFactory.begin()


async def create_all() -> None:
    """Create tables directly (tests only; production uses Alembic)."""
    from models import Base

    assert engine is not None
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    global engine, SessionFactory
    if engine is not None:
        await engine.dispose()
        log.info("Database connections closed")
    engine = None
    SessionFactory = None
