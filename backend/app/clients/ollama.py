import httpx

from app.core.config import Settings


def create_ollama_client(settings: Settings) -> httpx.AsyncClient | None:
    if settings.OLLAMA_BASE_URL is None:
        return None
    return httpx.AsyncClient(
        base_url=str(settings.OLLAMA_BASE_URL).rstrip("/"),
        timeout=settings.OLLAMA_TIMEOUT_SECONDS,
        follow_redirects=False,
        trust_env=False,
    )


async def check_ollama(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/tags")
    response.raise_for_status()

async def close_ollama(client: httpx.AsyncClient | None) -> None:
    if client is not None:
        await client.aclose()
