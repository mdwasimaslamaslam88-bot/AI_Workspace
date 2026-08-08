from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from app.clients.ollama import check_ollama
from app.clients.postgres import check_postgres
from app.clients.redis import check_redis
from app.core.logging import get_logger

router = APIRouter()
logger = get_logger(__name__)

@router.get("/health", tags=["Health"])
def read_health():
    return {"status": "healthy"}

@router.get("/health/live", tags=["Health"])
def read_liveness():
    return {"status": "alive"}

@router.get("/health/ready", tags=["Health"])
async def read_readiness(request: Request):
    dependencies = {"postgresql": getattr(request.app.state, "postgres_engine", None), "redis": getattr(request.app.state, "redis_client", None), "ollama": getattr(request.app.state, "ollama_client", None)}
    probes = {"postgresql": check_postgres, "redis": check_redis, "ollama": check_ollama}
    statuses: dict[str, dict[str, str]] = {}
    ready = True
    for name, client in dependencies.items():
        if client is None:
            statuses[name] = {"status": "unconfigured"}
            ready = False
            continue
        try:
            await probes[name](client)
        except Exception:
            logger.warning("dependency_unavailable", dependency=name)
            statuses[name] = {"status": "unavailable"}
            ready = False
        else:
            statuses[name] = {"status": "ready"}
    return JSONResponse(status_code=200 if ready else 503, content={"status": "ready" if ready else "not_ready", "dependencies": statuses})
