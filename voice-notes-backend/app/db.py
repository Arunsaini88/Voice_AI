# app/db.py
from urllib.parse import parse_qs, urlsplit, urlunsplit
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from app.config import settings
from app.models import Base


def _asyncpg_url_and_ssl(url: str) -> tuple[str, dict]:
    # Neon hands out postgresql://...?sslmode=require&channel_binding=require.
    # asyncpg needs the +asyncpg driver and rejects both query params, so strip
    # them and pass SSL through connect_args instead.
    u = urlsplit(url)
    scheme = "postgresql+asyncpg" if u.scheme in ("postgresql", "postgres") else u.scheme
    sslmode = parse_qs(u.query).get("sslmode", ["require"])[0]
    connect_args = {} if sslmode == "disable" else {"ssl": "require"}
    return urlunsplit((scheme, u.netloc, u.path, "", "")), connect_args


_url, _connect_args = _asyncpg_url_and_ssl(settings.DATABASE_URL)
engine = create_async_engine(_url, connect_args=_connect_args, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    async with SessionLocal() as session:
        yield session


async def init_db():
    # Good enough until there's a second migration; swap for Alembic then.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
