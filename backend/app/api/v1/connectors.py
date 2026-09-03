from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.connectors import ConnectorRuntime, ConnectorService
from app.connectors.catalog import (
    CONNECTOR_LIFECYCLE,
    CONNECTOR_PLATFORM_CAPABILITIES,
)
from app.connectors.credentials import OAuth2Credential, encode_oauth2_credential
from app.connectors.service import (
    ConnectorConflictError,
    ConnectorExecutionError,
    ConnectorNotFoundError,
)
from app.db.dependencies import get_db_session
from app.models.user import User
from app.schemas.connector import (
    ConnectorExecutionPageResponse,
    ConnectorExecutionRequest,
    ConnectorExecutionResponse,
    ConnectorExecutionResultResponse,
    ConnectorPageResponse,
    ConnectorPlatformCapabilityResponse,
    ConnectorPlatformResponse,
    ConnectorResponse,
    ConnectorSettingsResponse,
    ConnectorWriteRequest,
)
from app.models.connector import ConnectorAuthKind, ConnectorKind


router = APIRouter(prefix="/connectors", tags=["Connectors"])


def _runtime(request: Request, *, required: bool = True) -> ConnectorRuntime | None:
    runtime = getattr(request.app.state, "connector_runtime", None)
    if runtime is None and not required:
        return None
    if not isinstance(runtime, ConnectorRuntime):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Connector runtime is not configured",
        )
    return runtime


def _service(request: Request, session: AsyncSession) -> ConnectorService:
    runtime = _runtime(request)
    assert runtime is not None
    return ConnectorService(session, runtime)


def _connector_response(value) -> ConnectorResponse:
    return ConnectorResponse.model_validate(value, from_attributes=True)


def _execution_response(value) -> ConnectorExecutionResponse:
    return ConnectorExecutionResponse.model_validate(value, from_attributes=True)


def _write_arguments(payload: ConnectorWriteRequest) -> dict:
    credential = (
        payload.credential.get_secret_value()
        if payload.credential is not None
        else None
    )
    if payload.oauth2_credential is not None:
        oauth2 = payload.oauth2_credential
        credential = encode_oauth2_credential(
            OAuth2Credential(
                access_token=oauth2.access_token.get_secret_value(),
                refresh_token=(
                    oauth2.refresh_token.get_secret_value()
                    if oauth2.refresh_token is not None
                    else None
                ),
                client_id=oauth2.client_id,
                client_secret=(
                    oauth2.client_secret.get_secret_value()
                    if oauth2.client_secret is not None
                    else None
                ),
                token_path=oauth2.token_path,
                expires_at=oauth2.expires_at,
            )
        )
    return {
        "name": payload.name,
        "provider": payload.provider,
        "service": payload.service,
        "kind": payload.kind,
        "base_url": payload.base_url,
        "auth_kind": payload.auth_kind,
        "credential": credential,
        "scopes": tuple(payload.scopes),
        "capabilities": tuple(payload.capabilities),
        "path_prefixes": tuple(payload.path_prefixes),
        "health_path": payload.health_path,
        "discovery_path": payload.discovery_path,
        "enabled": payload.enabled,
        "timeout_seconds": payload.timeout_seconds,
        "max_retries": payload.max_retries,
        "rate_limit_requests_per_minute": payload.rate_limit_requests_per_minute,
    }


def _raise_execution_error(exc: ConnectorExecutionError) -> None:
    code = exc.execution.error_code
    status_code = {
        "connector_permission_denied": status.HTTP_403_FORBIDDEN,
        "connector_disabled": status.HTTP_409_CONFLICT,
        "connector_rate_limited": status.HTTP_429_TOO_MANY_REQUESTS,
        "connector_timed_out": status.HTTP_504_GATEWAY_TIMEOUT,
        "connector_circuit_open": status.HTTP_503_SERVICE_UNAVAILABLE,
    }.get(code, status.HTTP_502_BAD_GATEWAY)
    raise HTTPException(
        status_code=status_code,
        detail="Connector action failed",
        headers={"X-Connector-Execution-ID": str(exc.execution.id)},
    ) from None


@router.get("/settings", response_model=ConnectorSettingsResponse)
async def read_connector_settings(
    request: Request,
    response: Response,
    _current_user: Annotated[User, Depends(get_current_user)],
) -> ConnectorSettingsResponse:
    runtime = _runtime(request, required=False)
    response.headers["Cache-Control"] = "private, no-store"
    return ConnectorSettingsResponse(
        configured=runtime is not None,
        allowed_origins=sorted(runtime.allowed_origins) if runtime is not None else [],
        supported_kinds=list(ConnectorKind),
        supported_auth_kinds=list(ConnectorAuthKind),
    )


@router.get("/platform", response_model=ConnectorPlatformResponse)
async def read_connector_platform(
    response: Response,
    _current_user: Annotated[User, Depends(get_current_user)],
) -> ConnectorPlatformResponse:
    response.headers["Cache-Control"] = "private, no-store"
    return ConnectorPlatformResponse(
        lifecycle=list(CONNECTOR_LIFECYCLE),
        capabilities=[
            ConnectorPlatformCapabilityResponse.model_validate(capability)
            for capability in CONNECTOR_PLATFORM_CAPABILITIES
        ],
    )


@router.get("", response_model=ConnectorPageResponse)
async def list_connectors(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ConnectorPageResponse:
    values = await _service(request, session).list_for_owner(current_user.id)
    return ConnectorPageResponse(items=[_connector_response(value) for value in values])


@router.post("", response_model=ConnectorResponse, status_code=status.HTTP_201_CREATED)
async def create_connector(
    payload: ConnectorWriteRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ConnectorResponse:
    try:
        value = await _service(request, session).create_for_owner(
            current_user.id, **_write_arguments(payload)
        )
    except ConnectorConflictError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Connector registry is full",
        ) from None
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Connector policy is invalid",
        ) from None
    return _connector_response(value)


@router.get("/executions", response_model=ConnectorExecutionPageResponse)
async def list_connector_executions(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    connector_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ConnectorExecutionPageResponse:
    try:
        values = await _service(request, session).list_executions_for_owner(
            current_user.id, connector_id=connector_id, limit=limit
        )
    except ConnectorNotFoundError:
        raise HTTPException(status_code=404, detail="Connector not found") from None
    return ConnectorExecutionPageResponse(
        items=[_execution_response(value) for value in values]
    )


@router.get("/{connector_id}", response_model=ConnectorResponse)
async def get_connector(
    connector_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ConnectorResponse:
    try:
        value = await _service(request, session).get_for_owner(
            current_user.id, connector_id
        )
    except ConnectorNotFoundError:
        raise HTTPException(status_code=404, detail="Connector not found") from None
    return _connector_response(value)


@router.put("/{connector_id}", response_model=ConnectorResponse)
async def update_connector(
    connector_id: UUID,
    payload: ConnectorWriteRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ConnectorResponse:
    try:
        value = await _service(request, session).update_for_owner(
            current_user.id, connector_id, **_write_arguments(payload)
        )
    except ConnectorNotFoundError:
        raise HTTPException(status_code=404, detail="Connector not found") from None
    except ConnectorConflictError:
        raise HTTPException(status_code=409, detail="Connector is revoked") from None
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Connector policy is invalid") from None
    return _connector_response(value)


@router.delete("/{connector_id}", response_model=ConnectorResponse)
async def revoke_connector(
    connector_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ConnectorResponse:
    try:
        value = await _service(request, session).revoke_for_owner(
            current_user.id, connector_id
        )
    except ConnectorNotFoundError:
        raise HTTPException(status_code=404, detail="Connector not found") from None
    return _connector_response(value)


@router.post("/{connector_id}/health", response_model=ConnectorExecutionResultResponse)
async def check_connector_health(
    connector_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ConnectorExecutionResultResponse:
    try:
        value = await _service(request, session).health_for_owner(
            current_user.id, connector_id
        )
    except ConnectorNotFoundError:
        raise HTTPException(status_code=404, detail="Connector not found") from None
    except ConnectorExecutionError as exc:
        _raise_execution_error(exc)
    return ConnectorExecutionResultResponse(
        execution=_execution_response(value.execution), payload=value.payload
    )


@router.post("/{connector_id}/discover", response_model=ConnectorExecutionResultResponse)
async def discover_connector_capabilities(
    connector_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ConnectorExecutionResultResponse:
    try:
        value = await _service(request, session).discover_for_owner(
            current_user.id, connector_id
        )
    except ConnectorNotFoundError:
        raise HTTPException(status_code=404, detail="Connector not found") from None
    except ConnectorConflictError:
        raise HTTPException(
            status_code=409, detail="Connector discovery is not configured"
        ) from None
    except ConnectorExecutionError as exc:
        _raise_execution_error(exc)
    return ConnectorExecutionResultResponse(
        execution=_execution_response(value.execution), payload=value.payload
    )


@router.post("/{connector_id}/disconnect", response_model=ConnectorResponse)
async def disconnect_connector(
    connector_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ConnectorResponse:
    try:
        value = await _service(request, session).disconnect_for_owner(
            current_user.id, connector_id
        )
    except ConnectorNotFoundError:
        raise HTTPException(status_code=404, detail="Connector not found") from None
    except ConnectorConflictError:
        raise HTTPException(status_code=409, detail="Connector is revoked") from None
    return _connector_response(value)


@router.post("/{connector_id}/reconnect", response_model=ConnectorExecutionResultResponse)
async def reconnect_connector(
    connector_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ConnectorExecutionResultResponse:
    try:
        value = await _service(request, session).reconnect_for_owner(
            current_user.id, connector_id
        )
    except ConnectorNotFoundError:
        raise HTTPException(status_code=404, detail="Connector not found") from None
    except ConnectorConflictError:
        raise HTTPException(status_code=409, detail="Connector is revoked") from None
    except ConnectorExecutionError as exc:
        _raise_execution_error(exc)
    return ConnectorExecutionResultResponse(
        execution=_execution_response(value.execution), payload=value.payload
    )


@router.post(
    "/{connector_id}/executions",
    response_model=ConnectorExecutionResultResponse,
    status_code=status.HTTP_201_CREATED,
)
async def execute_connector(
    connector_id: UUID,
    payload: ConnectorExecutionRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ConnectorExecutionResultResponse:
    try:
        value = await _service(request, session).execute_for_owner(
            current_user.id,
            connector_id,
            method=payload.method,
            path=payload.path,
            json_body=payload.json_body,
            idempotency_key=payload.idempotency_key,
        )
    except ConnectorNotFoundError:
        raise HTTPException(status_code=404, detail="Connector not found") from None
    except ConnectorExecutionError as exc:
        _raise_execution_error(exc)
    except ValueError:
        raise HTTPException(status_code=422, detail="Connector request is invalid") from None
    return ConnectorExecutionResultResponse(
        execution=_execution_response(value.execution), payload=value.payload
    )
