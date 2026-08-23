import httpx

from app.core.config import Settings


def create_comfyui_client(settings: Settings) -> httpx.AsyncClient | None:
    if settings.COMFYUI_BASE_URL is None:
        return None
    return httpx.AsyncClient(
        base_url=str(settings.COMFYUI_BASE_URL).rstrip("/"),
        timeout=settings.COMFYUI_TIMEOUT_SECONDS,
        follow_redirects=False,
        trust_env=False,
    )


async def close_comfyui(client: httpx.AsyncClient | None) -> None:
    if client is not None:
        await client.aclose()
