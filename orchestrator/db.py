from typing import Optional
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncEngine,
    AsyncSession,
)


engine: Optional[AsyncEngine] = None
AsyncSessionLocal: Optional[async_sessionmaker[AsyncSession]] = None


def init_db(db_url: str) -> None:
    global engine, AsyncSessionLocal
    engine = create_async_engine(db_url, echo=False)
    AsyncSessionLocal = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )


def get_session() -> AsyncSession:
    if AsyncSessionLocal is None:
        raise RuntimeError("Database not initialized. Call init_db first.")
    return AsyncSessionLocal()
