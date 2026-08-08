from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

def create_session_factory(engine: AsyncEngine | None):
    return None if engine is None else async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

@asynccontextmanager
async def session_scope(factory) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        yield session
