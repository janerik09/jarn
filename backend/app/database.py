from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import NullPool
from app.config import settings

DATABASE_URL = settings.database_url.replace("postgresql://", "postgresql+asyncpg://")

engine = create_async_engine(
    DATABASE_URL,
    echo=settings.debug,
    poolclass=NullPool if settings.environment == "testing" else None,
    pool_pre_ping=True,
)

AsyncSessionLocal = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

Base = declarative_base()


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    """Bring the database schema up to date with Alembic (safe to run on every start)."""
    import asyncio
    from pathlib import Path
    from alembic import command
    from alembic.config import Config

    backend_dir = Path(__file__).resolve().parent.parent
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    await asyncio.to_thread(command.upgrade, config, "head")


async def close_db():
    await engine.dispose()
