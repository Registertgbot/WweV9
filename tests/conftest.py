import pytest_asyncio

import config
import database
from config import Settings


@pytest_asyncio.fixture
async def db_url(tmp_path):
    """Fresh SQLite database per test (tests need no Postgres server)."""
    url = f"sqlite+aiosqlite:///{tmp_path / 'arena.db'}"
    config.set_settings(Settings(database_url=url, restart_grace=30))
    database.init_engine(url)
    await database.create_all()
    yield url
    await database.dispose_engine()
    config.set_settings(None)


@pytest_asyncio.fixture
async def db(db_url):
    yield
