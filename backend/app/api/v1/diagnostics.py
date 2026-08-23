from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.ai.catalog import ModelAvailability, ModelCapability
from app.api.dependencies import get_current_user
from app.clients.ollama import check_ollama
from app.clients.postgres import check_postgres
from app.clients.redis import check_redis
from app.core.config import settings
from app.core.logging import get_logger
from app.hardware import HardwareInventory, hardware_class_for_vram
from app.models.user import User
from app.schemas.diagnostics import (
    DiagnosticStatus,
    GpuDiagnosticResponse,
    ServiceDiagnosticResponse,
    SystemDiagnosticsResponse,
)


router = APIRouter(prefix="/diagnostics", tags=["Diagnostics"])
logger = get_logger(__name__)


async def _probe(client: object | None, probe) -> DiagnosticStatus:
    if client is None:
        return "unconfigured"
    try:
        await probe(client)
    except Exception:
        logger.warning("private_diagnostic_unavailable")
        return "unavailable"
    return "ready"


def _runtime_status(
    runtime: object | None,
    models: tuple,
    capability: ModelCapability,
    *,
    catalog_status: DiagnosticStatus,
) -> DiagnosticStatus:
    if runtime is None:
        return "unconfigured"
    if catalog_status != "ready":
        return "unavailable"
    return (
        "ready"
        if any(
            model.availability is ModelAvailability.AVAILABLE
            and model.installed
            and model.runnable_now
            and capability in model.capabilities
            for model in models
        )
        else "unavailable"
    )


@router.get("", response_model=SystemDiagnosticsResponse)
async def read_private_diagnostics(
    request: Request,
    _current_user: Annotated[User, Depends(get_current_user)],
) -> SystemDiagnosticsResponse:
    postgres_status = await _probe(
        getattr(request.app.state, "postgres_engine", None),
        check_postgres,
    )
    redis_status = await _probe(
        getattr(request.app.state, "redis_client", None),
        check_redis,
    )
    ollama_status = await _probe(
        getattr(request.app.state, "ollama_client", None),
        check_ollama,
    )

    catalog = getattr(request.app.state, "model_catalog", None)
    models: tuple = ()
    catalog_status: DiagnosticStatus = "unconfigured"
    if catalog is not None:
        try:
            models = tuple(await catalog.list_models())
        except Exception:
            logger.warning("private_model_diagnostic_unavailable")
            catalog_status = "unavailable"
        else:
            catalog_status = "ready"

    vision_status: DiagnosticStatus
    if catalog_status == "unconfigured":
        vision_status = "unconfigured"
    elif catalog_status == "unavailable":
        vision_status = "unavailable"
    else:
        vision_status = (
            "ready"
            if any(
                model.availability is ModelAvailability.AVAILABLE
                and model.runnable_now
                and ModelCapability.TEXT_GENERATION in model.capabilities
                and ModelCapability.VISION_INPUT in model.capabilities
                for model in models
            )
            else "unavailable"
        )

    image_status = _runtime_status(
        getattr(request.app.state, "image_generation_runtime", None),
        models,
        ModelCapability.IMAGE_GENERATION,
        catalog_status=catalog_status,
    )
    speech_to_text_status = _runtime_status(
        getattr(request.app.state, "speech_recognition_runtime", None),
        models,
        ModelCapability.SPEECH_RECOGNITION,
        catalog_status=catalog_status,
    )
    text_to_speech_status = _runtime_status(
        getattr(request.app.state, "speech_synthesis_runtime", None),
        models,
        ModelCapability.SPEECH_SYNTHESIS,
        catalog_status=catalog_status,
    )

    hardware = getattr(request.app.state, "hardware_inventory", None)
    if not isinstance(hardware, HardwareInventory):
        hardware = None
    gpu_status: DiagnosticStatus = (
        "ready" if hardware is not None and hardware.gpu_vram_bytes else "unconfigured"
    )
    gpu_names = hardware.gpu_names if hardware is not None else ()
    gpu_vram = hardware.gpu_vram_bytes if hardware is not None else ()
    gpus = [
        GpuDiagnosticResponse(
            model=gpu_names[index] if index < len(gpu_names) else "NVIDIA GPU",
            vram_bytes=vram_bytes,
            hardware_class=hardware_class_for_vram(vram_bytes),
        )
        for index, vram_bytes in enumerate(gpu_vram[:16])
    ]

    remote_requested = settings.REMOTE_GATEWAY_MODE == "tailscale"
    remote_status: DiagnosticStatus = (
        "ready"
        if remote_requested and request.url.scheme == "https"
        else "unavailable" if remote_requested else "unconfigured"
    )
    return SystemDiagnosticsResponse(
        mode="remote" if remote_requested else "local",
        services=[
            ServiceDiagnosticResponse(id="backend", status="ready"),
            ServiceDiagnosticResponse(id="database", status=postgres_status),
            ServiceDiagnosticResponse(id="redis", status=redis_status),
            ServiceDiagnosticResponse(id="ollama", status=ollama_status),
            ServiceDiagnosticResponse(id="vision", status=vision_status),
            ServiceDiagnosticResponse(id="image_runtime", status=image_status),
            ServiceDiagnosticResponse(
                id="speech_to_text", status=speech_to_text_status
            ),
            ServiceDiagnosticResponse(
                id="text_to_speech", status=text_to_speech_status
            ),
            ServiceDiagnosticResponse(
                id="storage",
                status=(
                    "ready"
                    if getattr(request.app.state, "asset_storage", None) is not None
                    else "unconfigured"
                ),
            ),
            ServiceDiagnosticResponse(id="remote_gateway", status=remote_status),
            ServiceDiagnosticResponse(id="gpu", status=gpu_status),
        ],
        gpus=gpus,
    )
