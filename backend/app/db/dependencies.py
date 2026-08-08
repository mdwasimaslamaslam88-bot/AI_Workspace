from collections.abc import AsyncIterator
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory = getattr(request.app.state, "db_session_factory", None)
    if factory is None:
        raise RuntimeError("Database session factory is not configured")
    async with factory() as session:
        try:
            yield session
        except BaseException:
            await session.rollback()
            raise
