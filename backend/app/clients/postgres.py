from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from app.core.config import Settings

def create_postgres_engine(settings: Settings) -> AsyncEngine | None:
    if settings.DATABASE_URL is None:
        return None
    return create_async_engine(str(settings.DATABASE_URL), pool_pre_ping=True, connect_args={"timeout": settings.DATABASE_CONNECT_TIMEOUT_SECONDS})

async def check_postgres(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))

async def dispose_postgres(engine: AsyncEngine | None) -> None:
    if engine is not None:
        await engine.dispose()
