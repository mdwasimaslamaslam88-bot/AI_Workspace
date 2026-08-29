import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.ai.catalog import (
    ModelCapability,
    ModelCatalog,
    ModelModality,
    public_model_id,
)
from app.ai.generation import TextGenerationRouter
from app.ai.routing import ModelTask, TaskAwareModelRouter
from app.clients.comfyui import close_comfyui, create_comfyui_client
from app.clients.ollama import close_ollama, create_ollama_client
from app.clients.postgres import create_postgres_engine, dispose_postgres
from app.clients.redis import close_redis, create_redis_client
from app.core.config import Settings, settings
from app.db.session import create_session_factory
from app.hardware import detect_hardware
from app.hardware.planner import GIBIBYTE
from app.runtimes.configured_media import (
    ConfiguredMediaModel,
    ConfiguredMediaModelDiscoveryRuntime,
)
from app.runtimes.comfyui import ComfyUIImageRuntime
from app.runtimes.faster_whisper import FasterWhisperSpeechRecognitionRuntime
from app.runtimes.ollama import (
    OllamaModelDiscoveryRuntime,
    OllamaTextGenerationRuntime,
)
from app.runtimes.ollama_embedding import OllamaEmbeddingRuntime
from app.runtimes.piper import PiperSpeechSynthesisRuntime
from app.services.asset import reconcile_asset_storage
from app.services.generation_admission import GenerationAdmissionController
from app.services.tool import reconcile_tool_executions
from app.services.workflow import WorkflowRunner, reconcile_workflows
from app.storage.local import LocalAssetStorage


def _speech_runtimes():
    discovery_runtimes = []
    speech_recognition_runtime = None
    speech_synthesis_runtime = None
    worker = Path(__file__).resolve().parents[1] / "runtime_workers/faster_whisper.py"

    if (
        settings.STT_PYTHON is not None
        and settings.STT_MODEL_ROOT is not None
        and settings.STT_MODEL_REFERENCE is not None
    ):
        discovery_runtimes.append(
            ConfiguredMediaModelDiscoveryRuntime(
                "faster_whisper",
                (
                    ConfiguredMediaModel(
                        reference=settings.STT_MODEL_REFERENCE,
                        display_name="Faster Whisper Small English",
                        modality=ModelModality.AUDIO,
                        family="Whisper",
                        parameter_class="244M",
                        capabilities=(ModelCapability.SPEECH_RECOGNITION,),
                        required_vram_bytes=(
                            2 * GIBIBYTE if settings.STT_DEVICE == "cuda" else 0
                        ),
                        required_ram_bytes=2 * GIBIBYTE,
                        required_files=(
                            settings.STT_PYTHON,
                            settings.STT_MODEL_ROOT / "model.bin",
                            settings.STT_MODEL_ROOT / "config.json",
                            settings.STT_MODEL_ROOT / "tokenizer.json",
                            settings.STT_MODEL_ROOT / "vocabulary.txt",
                        ),
                        required_directories=settings.STT_LIBRARY_DIRECTORIES,
                    ),
                ),
            )
        )
        try:
            speech_recognition_runtime = FasterWhisperSpeechRecognitionRuntime(
                settings.STT_PYTHON,
                worker,
                settings.STT_MODEL_ROOT,
                model_reference=settings.STT_MODEL_REFERENCE,
                device=settings.STT_DEVICE,
                compute_type=settings.STT_COMPUTE_TYPE,
                library_directories=settings.STT_LIBRARY_DIRECTORIES,
                timeout_seconds=settings.STT_TIMEOUT_SECONDS,
                max_active=settings.STT_MAX_ACTIVE_PER_PROCESS,
            )
        except (OSError, ValueError):
            speech_recognition_runtime = None

    if (
        settings.TTS_PIPER_BINARY is not None
        and settings.TTS_VOICE_MODEL is not None
        and settings.TTS_VOICE_CONFIG is not None
        and settings.TTS_VOICE_REFERENCE is not None
    ):
        discovery_runtimes.append(
            ConfiguredMediaModelDiscoveryRuntime(
                "piper",
                (
                    ConfiguredMediaModel(
                        reference=settings.TTS_VOICE_REFERENCE,
                        display_name="Piper Lessac Medium",
                        modality=ModelModality.AUDIO,
                        family="Piper",
                        parameter_class="medium",
                        capabilities=(ModelCapability.SPEECH_SYNTHESIS,),
                        required_vram_bytes=0,
                        required_ram_bytes=512 * 1024**2,
                        required_files=(
                            settings.TTS_PIPER_BINARY,
                            settings.TTS_VOICE_MODEL,
                            settings.TTS_VOICE_CONFIG,
                        ),
                    ),
                ),
            )
        )
        try:
            speech_synthesis_runtime = PiperSpeechSynthesisRuntime(
                settings.TTS_PIPER_BINARY,
                settings.TTS_VOICE_MODEL,
                settings.TTS_VOICE_CONFIG,
                model_reference=settings.TTS_VOICE_REFERENCE,
                timeout_seconds=settings.TTS_TIMEOUT_SECONDS,
                max_active=settings.TTS_MAX_ACTIVE_PER_PROCESS,
            )
        except (OSError, ValueError):
            speech_synthesis_runtime = None

    return (
        tuple(discovery_runtimes),
        speech_recognition_runtime,
        speech_synthesis_runtime,
    )


def _require_user_provisioning_database(configured: Settings) -> None:
    if (
        configured.USER_PROVISIONING_TOKEN_DIGEST is not None
        and configured.DATABASE_URL is None
    ):
        raise RuntimeError(
            "DATABASE_URL must be configured when user provisioning is enabled"
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    _require_user_provisioning_database(settings)
    async with AsyncExitStack() as resource_stack:
        hardware_inventory = await asyncio.to_thread(detect_hardware)
        app.state.hardware_inventory = hardware_inventory
        postgres_engine = create_postgres_engine(settings)
        resource_stack.push_async_callback(
            dispose_postgres,
            postgres_engine,
        )
        redis_client = create_redis_client(settings)
        resource_stack.push_async_callback(close_redis, redis_client)
        ollama_client = create_ollama_client(settings)
        resource_stack.push_async_callback(close_ollama, ollama_client)
        comfyui_client = create_comfyui_client(settings)
        resource_stack.push_async_callback(close_comfyui, comfyui_client)

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
        app.state.comfyui_client = comfyui_client
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
        app.state.document_embedding_runtime = (
            OllamaEmbeddingRuntime(
                ollama_client,
                settings.OLLAMA_EMBEDDING_MODEL,
                timeout_seconds=settings.OLLAMA_EMBEDDING_TIMEOUT_SECONDS,
                max_request_bytes=settings.OLLAMA_EMBEDDING_MAX_REQUEST_BYTES,
                max_response_bytes=settings.OLLAMA_EMBEDDING_MAX_RESPONSE_BYTES,
                batch_size=settings.OLLAMA_EMBEDDING_BATCH_SIZE,
                max_active=settings.OLLAMA_EMBEDDING_MAX_ACTIVE_PER_PROCESS,
                keep_alive_seconds=settings.OLLAMA_KEEP_ALIVE_SECONDS,
            )
            if ollama_client is not None
            and settings.OLLAMA_EMBEDDING_MODEL is not None
            else None
        )
        (
            speech_discovery_runtimes,
            app.state.speech_recognition_runtime,
            app.state.speech_synthesis_runtime,
        ) = _speech_runtimes()
        image_runtime = None
        if (
            comfyui_client is not None
            and settings.COMFYUI_CHECKPOINT is not None
            and settings.COMFYUI_INPUT_ROOT is not None
            and settings.COMFYUI_TEMP_ROOT is not None
            and settings.COMFYUI_MODEL_REFERENCE is not None
        ):
            try:
                image_runtime = ComfyUIImageRuntime(
                    comfyui_client,
                    settings.COMFYUI_CHECKPOINT,
                    settings.COMFYUI_INPUT_ROOT,
                    settings.COMFYUI_TEMP_ROOT,
                    model_reference=settings.COMFYUI_MODEL_REFERENCE,
                    timeout_seconds=settings.COMFYUI_TIMEOUT_SECONDS,
                    max_active=settings.COMFYUI_MAX_ACTIVE_PER_PROCESS,
                )
            except (OSError, ValueError):
                image_runtime = None
        app.state.image_generation_runtime = image_runtime
        app.state.image_editing_runtime = image_runtime
        app.state.workflow_tasks = {}
        app.state.workflow_runner = (
            WorkflowRunner(
                app.state.db_session_factory,
                asyncio.Semaphore(2),
                app.state.workflow_tasks,
                document_storage=asset_storage,
                document_admission=app.state.document_ingestion_admission,
                document_embedding_runtime=app.state.document_embedding_runtime,
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
                else ()
            )
            + speech_discovery_runtimes
            + ((image_runtime,) if image_runtime is not None else ()),
            max_list_discovery_seconds=(
                settings.MODEL_LIST_MAX_DISCOVERY_SECONDS
            ),
            hardware_inventory=hardware_inventory,
        )
        app.state.task_model_router = TaskAwareModelRouter(
            {
                ModelTask(task): public_model_id("ollama-local", reference)
                for task, reference in settings.OLLAMA_TASK_MODEL_PREFERENCES.items()
            }
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
                    keep_alive_seconds=settings.OLLAMA_KEEP_ALIVE_SECONDS,
                ),
            )
            if ollama_client is not None
            else ()
        )
        yield
