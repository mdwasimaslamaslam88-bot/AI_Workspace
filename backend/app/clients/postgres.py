import ssl

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from app.core.config import Settings


def build_postgres_connect_args(settings: Settings) -> dict[str, object]:
    ssl_mode = settings.DATABASE_SSL_MODE
    if ssl_mode == "disable":
        ssl_value: bool | ssl.SSLContext = False
    elif ssl_mode == "require":
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        ssl_value = ssl_context
    else:
        ssl_context = ssl.create_default_context(cafile=settings.DATABASE_SSL_ROOT_CERT)
        ssl_context.check_hostname = ssl_mode == "verify-full"
        ssl_context.verify_mode = ssl.CERT_REQUIRED
        ssl_value = ssl_context

    return {
        "timeout": settings.DATABASE_CONNECT_TIMEOUT_SECONDS,
        "command_timeout": settings.DATABASE_COMMAND_TIMEOUT_SECONDS,
        "ssl": ssl_value,
    }


def create_postgres_engine(settings: Settings) -> AsyncEngine | None:
    if settings.DATABASE_URL is None:
        return None
    return create_async_engine(
        str(settings.DATABASE_URL),
        pool_pre_ping=True,
        pool_size=settings.DATABASE_POOL_SIZE,
        max_overflow=settings.DATABASE_MAX_OVERFLOW,
        pool_timeout=settings.DATABASE_POOL_TIMEOUT_SECONDS,
        connect_args=build_postgres_connect_args(settings),
    )

async def check_postgres(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))

async def dispose_postgres(engine: AsyncEngine | None) -> None:
    if engine is not None:
        await engine.dispose()
