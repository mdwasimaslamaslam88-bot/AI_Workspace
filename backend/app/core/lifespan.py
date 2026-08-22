import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

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
from app.services.asset import reconcile_asset_storage
from app.services.generation_admission import GenerationAdmissionController
from app.services.tool import reconcile_tool_executions
from app.services.workflow import WorkflowRunner, reconcile_workflows
from app.storage.local import LocalAssetStorage


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with AsyncExitStack() as resource_stack:
        postgres_engine = create_postgres_engine(settings)
        resource_stack.push_async_callback(
            dispose_postgres,
            postgres_engine,
        )
        redis_client = create_redis_client(settings)
        resource_stack.push_async_callback(close_redis, redis_client)
        ollama_client = create_ollama_client(settings)
        resource_stack.push_async_callback(close_ollama, ollama_client)

        app.state.postgres_engine = postgres_engine
        app.state.db_session_factory = create_session_factory(postgres_engine)
        await reconcile_tool_executions(app.state.db_session_factory)
        await reconcile_workflows(app.state.db_session_factory)
        asset_storage = None
        if settings.ASSET_STORAGE_ROOT is not None:
            asset_storage = LocalAssetStorage(settings.ASSET_STORAGE_ROOT)
            await reconcile_asset_storage(
                app.state.db_session_factory, asset_storage
            )
        app.state.asset_storage = asset_storage
        app.state.redis_client = redis_client
        app.state.ollama_client = ollama_client
        app.state.generation_admission_controller = GenerationAdmissionController(
            settings.GENERATION_MAX_ACTIVE_PER_PROCESS
        )
        app.state.generation_max_duration_seconds = (
            settings.GENERATION_MAX_DURATION_SECONDS
        )
        app.state.document_ingestion_admission = asyncio.Semaphore(
            settings.DOCUMENT_INGESTION_MAX_ACTIVE_PER_PROCESS
        )
        app.state.document_ingestion_tasks = {}
        app.state.document_ingestion_max_duration_seconds = (
            settings.DOCUMENT_INGESTION_MAX_DURATION_SECONDS
        )
        app.state.workflow_tasks = {}
        app.state.workflow_runner = (
            WorkflowRunner(
                app.state.db_session_factory,
                asyncio.Semaphore(2),
                app.state.workflow_tasks,
                document_storage=asset_storage,
                document_admission=app.state.document_ingestion_admission,
            )
            if app.state.db_session_factory is not None
            else None
        )
        if app.state.workflow_runner is not None:
            resource_stack.push_async_callback(
                app.state.workflow_runner.shutdown
            )
        app.state.model_list_max_response_bytes = (
            settings.MODEL_LIST_MAX_RESPONSE_BYTES
        )
        app.state.model_catalog = ModelCatalog(
            (
                OllamaModelDiscoveryRuntime(
                    ollama_client,
                    settings.OLLAMA_LOCAL_MODEL_ALLOWLIST,
                    max_response_bytes=(
                        settings.OLLAMA_CATALOG_MAX_RESPONSE_BYTES
                    ),
                    max_list_models=(
                        settings.OLLAMA_CATALOG_MAX_LIST_MODELS
                    ),
                ),
            )
            if ollama_client is not None
            else (),
            max_list_discovery_seconds=(
                settings.MODEL_LIST_MAX_DISCOVERY_SECONDS
            ),
        )
        app.state.text_generation_router = TextGenerationRouter(
            (
                OllamaTextGenerationRuntime(
                    ollama_client,
                    settings.OLLAMA_GENERATION_TIMEOUT_SECONDS,
                    settings.OLLAMA_LOCAL_MODEL_ALLOWLIST,
                    max_request_bytes=(
                        settings.OLLAMA_GENERATION_MAX_REQUEST_BYTES
                    ),
                    max_response_bytes=(
                        settings.OLLAMA_GENERATION_MAX_RESPONSE_BYTES
                    ),
                ),
            )
            if ollama_client is not None
            else ()
        )
        yield
