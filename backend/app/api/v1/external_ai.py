from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status

from app.api.dependencies import get_current_user
from app.external_ai import ExternalAIService, ExternalModelPolicy, ExternalProviderConfig, ExternalProviderKind
from app.external_ai.evidence import ExternalEvidenceError
from app.models.user import User
from app.schemas.external_ai import (
    ExternalAIGlobalUpdateRequest,
    ExternalAISettingsResponse,
    ExternalModelPolicyResponse,
    ExternalProviderDiscoveryResponse,
    ExternalProviderHealthResponse,
    ExternalProviderResponse,
    ExternalProviderUpsertRequest,
)


router = APIRouter(prefix="/external-ai", tags=["External AI"])


def _service(request: Request, *, required: bool = True) -> ExternalAIService | None:
    service = getattr(request.app.state, "external_ai_service", None)
    if service is None and not required:
        return None
    if not isinstance(service, ExternalAIService):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="External AI vault is not configured")
    return service


def _provider_response(view) -> ExternalProviderResponse:
    return ExternalProviderResponse(
        provider_id=view.provider_id,
        kind=view.kind,
        enabled=view.enabled,
        key_configured=view.key_configured,
        free_tier=view.free_tier,
        priority=view.priority,
        timeout_seconds=view.timeout_seconds,
        rate_limit_requests_per_minute=view.rate_limit_requests_per_minute,
        spending_limit_micros=view.spending_limit_micros,
        spent_micros=view.spent_micros,
        quota_remaining_tokens=view.quota_remaining_tokens,
        status=view.status,
        models=[
            ExternalModelPolicyResponse(
                model_id=model.model_id,
                tasks=sorted(model.tasks, key=lambda task: task.value),
                verified=model.verified,
                verification_evidence_sha256=model.verification_evidence_sha256,
                measured_quality=model.measured_quality,
                measured_latency_ms=model.measured_latency_ms,
                stability_rate=model.stability_rate,
                context_window=model.context_window,
                input_cost_micros_per_million_tokens=model.input_cost_micros_per_million_tokens,
                output_cost_micros_per_million_tokens=model.output_cost_micros_per_million_tokens,
            )
            for model in view.models
        ],
    )


def _settings(service: ExternalAIService | None) -> ExternalAISettingsResponse:
    views = service.provider_views() if service is not None else ()
    return ExternalAISettingsResponse(
        configured=service is not None,
        global_enabled=service.global_enabled() if service is not None else False,
        providers=[_provider_response(view) for view in views],
        supported_provider_kinds=list(ExternalProviderKind),
    )


@router.get("/settings", response_model=ExternalAISettingsResponse)
async def read_external_ai_settings(
    request: Request,
    response: Response,
    _current_user: Annotated[User, Depends(get_current_user)],
) -> ExternalAISettingsResponse:
    response.headers["Cache-Control"] = "private, no-store"
    return _settings(_service(request, required=False))


@router.put("/settings", response_model=ExternalAISettingsResponse)
async def update_external_ai_settings(
    payload: ExternalAIGlobalUpdateRequest,
    request: Request,
    response: Response,
    _current_user: Annotated[User, Depends(get_current_user)],
) -> ExternalAISettingsResponse:
    service = _service(request)
    assert service is not None
    service.vault.set_global_enabled(payload.enabled)
    response.headers["Cache-Control"] = "no-store"
    return _settings(service)


@router.put("/providers/{provider_id}", response_model=ExternalAISettingsResponse)
async def upsert_external_ai_provider(
    provider_id: Annotated[str, Path(pattern=r"^[a-z][a-z0-9_-]{0,63}$")],
    payload: ExternalProviderUpsertRequest,
    request: Request,
    response: Response,
    _current_user: Annotated[User, Depends(get_current_user)],
) -> ExternalAISettingsResponse:
    service = _service(request)
    assert service is not None
    try:
        models = []
        for model in payload.models:
            if model.verified:
                if model.verification_evidence_sha256 is None:
                    raise ValueError("verified model evidence is required")
                resolved = service.vault.evidence.resolve_policy(
                    payload.kind,
                    model.verification_evidence_sha256,
                )
                if (
                    resolved.model_id != model.model_id
                    or resolved.tasks != frozenset(model.tasks)
                ):
                    raise ExternalEvidenceError(
                        "external model identity does not match its evidence"
                    )
                models.append(resolved)
            else:
                models.append(
                    ExternalModelPolicy(
                        model_id=model.model_id,
                        tasks=frozenset(model.tasks),
                        verified=False,
                        measured_quality=model.measured_quality,
                        measured_latency_ms=model.measured_latency_ms,
                        stability_rate=model.stability_rate,
                        context_window=model.context_window,
                        input_cost_micros_per_million_tokens=(
                            model.input_cost_micros_per_million_tokens
                        ),
                        output_cost_micros_per_million_tokens=(
                            model.output_cost_micros_per_million_tokens
                        ),
                    )
                )
        config = ExternalProviderConfig(
            provider_id=provider_id,
            kind=payload.kind,
            enabled=payload.enabled,
            free_tier=payload.free_tier,
            priority=payload.priority,
            timeout_seconds=payload.timeout_seconds,
            rate_limit_requests_per_minute=payload.rate_limit_requests_per_minute,
            spending_limit_micros=payload.spending_limit_micros,
            quota_remaining_tokens=payload.quota_remaining_tokens,
            models=tuple(models),
        )
        service.vault.upsert_provider(
            config,
            **({"api_key": payload.api_key.get_secret_value()} if payload.api_key is not None else {}),
        )
    except (ValueError, ExternalEvidenceError):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="External provider configuration is invalid") from None
    response.headers["Cache-Control"] = "no-store"
    return _settings(service)


@router.delete("/providers/{provider_id}", response_model=ExternalAISettingsResponse)
async def delete_external_ai_provider(
    provider_id: Annotated[str, Path(pattern=r"^[a-z][a-z0-9_-]{0,63}$")],
    request: Request,
    response: Response,
    _current_user: Annotated[User, Depends(get_current_user)],
) -> ExternalAISettingsResponse:
    service = _service(request)
    assert service is not None
    try:
        service.vault.delete_provider(provider_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="External provider not found") from None
    response.headers["Cache-Control"] = "no-store"
    return _settings(service)


@router.post("/providers/{provider_id}/health", response_model=ExternalProviderHealthResponse)
async def check_external_ai_provider_health(
    provider_id: Annotated[str, Path(pattern=r"^[a-z][a-z0-9_-]{0,63}$")],
    request: Request,
    _current_user: Annotated[User, Depends(get_current_user)],
) -> ExternalProviderHealthResponse:
    service = _service(request)
    assert service is not None
    try:
        provider_status = await service.health(provider_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="External provider not found") from None
    return ExternalProviderHealthResponse(provider_id=provider_id, status=provider_status)


@router.post("/providers/{provider_id}/discover", response_model=ExternalProviderDiscoveryResponse)
async def discover_external_ai_provider_models(
    provider_id: Annotated[str, Path(pattern=r"^[a-z][a-z0-9_-]{0,63}$")],
    request: Request,
    _current_user: Annotated[User, Depends(get_current_user)],
) -> ExternalProviderDiscoveryResponse:
    service = _service(request)
    assert service is not None
    try:
        models = await service.discover_models(provider_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="External provider not found") from None
    except Exception:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="External provider discovery unavailable") from None
    return ExternalProviderDiscoveryResponse(provider_id=provider_id, discovered_model_ids=list(models))
