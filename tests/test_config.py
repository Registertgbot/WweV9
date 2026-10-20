from config import normalize_database_url


def test_railway_postgres_url_is_converted():
    url, ssl = normalize_database_url("postgres://u:p@host:5432/db")
    assert url == "postgresql+asyncpg://u:p@host:5432/db" and ssl is False


def test_postgresql_scheme_and_sslmode():
    url, ssl = normalize_database_url("postgresql://u:p@host/db?sslmode=require")
    assert url == "postgresql+asyncpg://u:p@host/db" and ssl is True


def test_existing_async_url_untouched():
    assert normalize_database_url("postgresql+asyncpg://u:p@h/db")[0] == "postgresql+asyncpg://u:p@h/db"
    assert normalize_database_url("sqlite+aiosqlite:///x.db")[0] == "sqlite+aiosqlite:///x.db"


def test_empty_url():
    assert normalize_database_url("") == ("", False)
