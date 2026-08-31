import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.ai.admission import (
    ModelAdmissionReason,
    ModelEligibilityStatus,
    PerformanceClass,
)
from app.ai.catalog import ModelAvailability, ModelCapability
from app.ai.routing import ModelRoutingUnavailableError, ModelTask
from app.api.dependencies import get_current_user
from app.clients.ollama import check_ollama
from app.clients.postgres import check_postgres
from app.clients.redis import check_redis
from app.core.config import settings
from app.core.logging import get_logger
from app.hardware import HardwareInventory, hardware_class_for_vram, hardware_profile
from app.models.user import User
from app.agent_os.contracts import AgentRunStatus
from app.agent_os.runtime import AgentRunManager
from app.external_ai.service import ExternalAIService
from app.maintenance import SelfUpdateError, SelfUpdateManager, UpdateState, UpdateStatus
from app.security_events import SecurityEventRecorder
from app.schemas.diagnostics import (
    AgentRuntimeDiagnosticResponse,
    DiagnosticStatus,
    GpuDiagnosticResponse,
    HardwareDiagnosticResponse,
    ExternalProviderDiagnosticResponse,
    ModelEligibilityDiagnosticResponse,
    ModelRouteDiagnosticResponse,
    ServiceDiagnosticResponse,
    SecurityEventDiagnosticResponse,
    SelfUpdateDiagnosticResponse,
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


def _tuple_value(values: tuple, index: int):
    return values[index] if index < len(values) else None


def _hardware_diagnostic(request: Request) -> HardwareDiagnosticResponse | None:
    hardware = getattr(request.app.state, "hardware_inventory", None)
    if not isinstance(hardware, HardwareInventory):
        return None
    capability_state = getattr(request.app.state, "hardware_capability_state", None)
    profile = hardware_profile(hardware)
    return HardwareDiagnosticResponse(
        fingerprint=hardware.fingerprint,
        profile_gib=profile.profile_gib,
        gpu_count=profile.gpu_count,
        total_ram_bytes=hardware.total_ram_bytes,
        available_ram_bytes=hardware.available_ram_bytes,
        swap_total_bytes=hardware.swap_total_bytes,
        swap_free_bytes=hardware.swap_free_bytes,
        storage_total_bytes=hardware.storage_total_bytes,
        storage_free_bytes=hardware.storage_free_bytes,
        cpu_model=hardware.cpu_model,
        cpu_logical_count=hardware.cpu_logical_count,
        os_name=hardware.os_name,
        os_version=hardware.os_version,
        architecture=hardware.architecture,
        upgrade_detected=bool(
            getattr(capability_state, "upgrade_detected", False)
        ),
        capability_cache_invalidated=bool(
            getattr(capability_state, "cache_invalidated", False)
        ),
        restart_required=bool(
            getattr(capability_state, "restart_required", False)
        ),
        runtime_validated=bool(
            getattr(request.app.state, "hardware_runtime_validated", True)
        ),
    )


@router.get("", response_model=SystemDiagnosticsResponse)
async def read_private_diagnostics(
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
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
            free_vram_bytes=_tuple_value(hardware.gpu_free_vram_bytes, index),
            vendor=_tuple_value(hardware.gpu_vendors, index),
            compute_capability=_tuple_value(
                hardware.gpu_compute_capabilities, index
            ),
            driver_version=_tuple_value(hardware.gpu_driver_versions, index),
            runtime=_tuple_value(hardware.accelerator_runtime_names, index),
            runtime_version=_tuple_value(
                hardware.accelerator_runtime_versions, index
            ),
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
    model_diagnostics = [
        ModelEligibilityDiagnosticResponse(
            model_id=model.model_id,
            display_name=model.display_name,
            runtime_id=model.runtime_id,
            status=getattr(
                model,
                "eligibility_status",
                ModelEligibilityStatus.RUNNABLE_NOW
                if model.runnable_now
                else ModelEligibilityStatus.RUNTIME_INCOMPATIBLE,
            ),
            reasons=list(
                getattr(
                    model,
                    "eligibility_reasons",
                    (
                        ModelAdmissionReason.ELIGIBLE
                        if model.runnable_now
                        else ModelAdmissionReason.MODEL_UNAVAILABLE,
                    ),
                )
            ),
            performance=getattr(
                model,
                "performance_class",
                PerformanceClass.INTERACTIVE
                if model.runnable_now
                else PerformanceClass.UNSUPPORTED,
            ),
            verified=bool(getattr(model, "verified", False)),
            fallback_model_id=getattr(model, "fallback_model_id", None),
        )
        for model in models[:256]
        if all(
            isinstance(getattr(model, name, None), str)
            for name in ("model_id", "display_name", "runtime_id")
        )
    ]
    route_diagnostics: list[ModelRouteDiagnosticResponse] = []
    task_router = getattr(request.app.state, "task_model_router", None)
    if task_router is not None and models:
        for task in ModelTask:
            try:
                decision = task_router.select(models, task)
            except (ModelRoutingUnavailableError, TypeError, ValueError):
                continue
            route_diagnostics.append(
                ModelRouteDiagnosticResponse(
                    task=task,
                    model_id=decision.model_id,
                    fallback_model_ids=list(decision.fallback_model_ids),
                    inference_mode=decision.inference_mode,
                )
            )

    external_service = getattr(request.app.state, "external_ai_service", None)
    external_providers = []
    if isinstance(external_service, ExternalAIService):
        external_providers = [
            ExternalProviderDiagnosticResponse(
                provider_id=provider.provider_id,
                status=provider.status,
                spent_micros=provider.spent_micros,
                spending_limit_micros=provider.spending_limit_micros,
                quota_remaining_tokens=provider.quota_remaining_tokens,
                verified_model_count=sum(model.verified for model in provider.models),
            )
            for provider in external_service.provider_views()
        ]

    agent_manager = getattr(request.app.state, "agent_run_manager", None)
    agent_diagnostic = None
    if isinstance(agent_manager, AgentRunManager):
        retained = await agent_manager.list_for_owner(current_user.id, limit=100)
        agent_diagnostic = AgentRuntimeDiagnosticResponse(
            active_count=sum(
                record.status
                in {
                    AgentRunStatus.QUEUED,
                    AgentRunStatus.PLANNING,
                    AgentRunStatus.RUNNING,
                    AgentRunStatus.VERIFYING,
                    AgentRunStatus.RETRYING,
                }
                for record in retained
            ),
            retained_count=len(retained),
            statuses={
                status: sum(record.status is status for record in retained)
                for status in AgentRunStatus
                if any(record.status is status for record in retained)
            },
        )

    update_manager = getattr(request.app.state, "self_update_manager", None)
    update_state = UpdateState()
    if isinstance(update_manager, SelfUpdateManager):
        try:
            update_state = update_manager.state()
        except SelfUpdateError:
            update_state = UpdateState(
                status=UpdateStatus.FAILED,
                failure_code="update_state_unavailable",
            )
    checkpoint_ready = bool(
        update_state.checkpoint_id is not None
        and update_state.status
        in {
            UpdateStatus.READY,
            UpdateStatus.ACTIVATED,
            UpdateStatus.ROLLED_BACK,
            UpdateStatus.CANCELLED,
        }
    )
    update_diagnostic = SelfUpdateDiagnosticResponse(
        configured=isinstance(update_manager, SelfUpdateManager),
        status=update_state.status,
        checkpoint_ready=checkpoint_ready,
        rollback_ready=checkpoint_ready,
    )
    recorder = getattr(request.app.state, "security_event_recorder", None)
    security_events = (
        [
            SecurityEventDiagnosticResponse(
                kind=event.kind,
                occurred_at=event.occurred_at.isoformat(),
            )
            for event in recorder.snapshot(limit=100)
        ]
        if isinstance(recorder, SecurityEventRecorder)
        else []
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
        hardware=_hardware_diagnostic(request),
        models=model_diagnostics,
        routes=route_diagnostics,
        external_providers=external_providers,
        agents=agent_diagnostic,
        self_update=update_diagnostic,
        security_events=security_events,
    )


@router.post(
    "/hardware/refresh",
    response_model=HardwareDiagnosticResponse,
)
async def refresh_private_hardware(
    request: Request,
    _current_user: Annotated[User, Depends(get_current_user)],
) -> HardwareDiagnosticResponse:
    service = getattr(request.app.state, "hardware_capability_service", None)
    if service is None:
        raise RuntimeError("Hardware capability service is not configured")
    state = await asyncio.to_thread(service.refresh)
    request.app.state.hardware_capability_state = state
    response = _hardware_diagnostic(request)
    if response is None:
        raise RuntimeError("Hardware capability state is unavailable")
    return response
