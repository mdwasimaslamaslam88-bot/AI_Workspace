from redis.asyncio import Redis
from app.core.config import Settings

def create_redis_client(settings: Settings) -> Redis | None:
    if settings.REDIS_URL is None:
        return None
    timeout = settings.REDIS_CONNECT_TIMEOUT_SECONDS
    return Redis.from_url(str(settings.REDIS_URL), decode_responses=True, socket_connect_timeout=timeout, socket_timeout=timeout)

async def check_redis(client: Redis) -> None:
    await client.ping()

async def close_redis(client: Redis | None) -> None:
    if client is not None:
        await client.aclose()
