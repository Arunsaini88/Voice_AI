# app/db.py
from urllib.parse import parse_qs, urlsplit, urlunsplit
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from app.config import settings


def _engine_url_and_args(url: str) -> tuple[str, dict]:
    u = urlsplit(url)
    if u.scheme.startswith("sqlite"):
        # Test suite only (sqlite+aiosqlite:///path). No SSL, and NullPool so a
        # connection never outlives the event loop that opened it.
        return url, {"poolclass": NullPool}
    # Neon hands out postgresql://...?sslmode=require&channel_binding=require.
    # asyncpg needs the +asyncpg driver and rejects both query params, so strip
    # them and pass SSL through connect_args instead.
    scheme = "postgresql+asyncpg" if u.scheme in ("postgresql", "postgres") else u.scheme
    sslmode = parse_qs(u.query).get("sslmode", ["require"])[0]
    connect_args = {} if sslmode == "disable" else {"ssl": "require"}
    return urlunsplit((scheme, u.netloc, u.path, "", "")), {"connect_args": connect_args, "pool_pre_ping": True}


_url, _engine_args = _engine_url_and_args(settings.DATABASE_URL)
engine = create_async_engine(_url, **_engine_args)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    async with SessionLocal() as session:
        yield session


async def schema_ready() -> bool:
    """True if the tables exist. Schema is managed by Alembic (`alembic upgrade
    head`), not created at startup, so a fresh database fails fast with a clear
    message instead of 500s on the first request."""
    def _check(conn) -> bool:
        insp = inspect(conn)
        return insp.has_table("users") and insp.has_table("notes")

    async with engine.connect() as conn:
        return await conn.run_sync(_check)
