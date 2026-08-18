from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.ai.catalog import ModelCatalog
from app.ai.generation import TextGenerationRouter
from app.clients.ollama import close_ollama, create_ollama_client
from app.clients.postgres import create_postgres_engine, dispose_postgres
from app.clients.redis import close_redis, create_redis_client
from app.core.config import settings
from app.db.session import create_session_factory
from app.runtimes.ollama import (
    OllamaModelDiscoveryRuntime,
    OllamaTextGenerationRuntime,
)
from app.services.generation_admission import GenerationAdmissionController


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    postgres_engine = create_postgres_engine(settings)
    redis_client = create_redis_client(settings)
    ollama_client = create_ollama_client(settings)
    app.state.postgres_engine = postgres_engine
    app.state.db_session_factory = create_session_factory(postgres_engine)
    app.state.redis_client = redis_client
    app.state.ollama_client = ollama_client
    app.state.generation_admission_controller = GenerationAdmissionController(
        settings.GENERATION_MAX_ACTIVE_PER_PROCESS
    )
    app.state.model_catalog = ModelCatalog(
        (
            OllamaModelDiscoveryRuntime(
                ollama_client,
                settings.OLLAMA_LOCAL_MODEL_ALLOWLIST,
            ),
        )
        if ollama_client is not None
        else ()
    )
    app.state.text_generation_router = TextGenerationRouter(
        (
            OllamaTextGenerationRuntime(
                ollama_client,
                settings.OLLAMA_GENERATION_TIMEOUT_SECONDS,
                settings.OLLAMA_LOCAL_MODEL_ALLOWLIST,
                max_response_bytes=(
                    settings.OLLAMA_GENERATION_MAX_RESPONSE_BYTES
                ),
            ),
        )
        if ollama_client is not None
        else ()
    )
    try:
        yield
    finally:
        await close_ollama(ollama_client)
        await close_redis(redis_client)
        await dispose_postgres(postgres_engine)
