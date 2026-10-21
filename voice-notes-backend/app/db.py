# app/db.py
from urllib.parse import parse_qs, urlsplit, urlunsplit
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from app.config import settings
from app.models import Base


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


async def init_db():
    # Good enough until there's a second migration; swap for Alembic then.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
