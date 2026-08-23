from __future__ import annotations

import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.audio import SpeechRuntimeInputError, SpeechRuntimeUnavailableError
from app.db.dependencies import get_db_session
from app.models.user import User
from app.schemas.asset import AssetResponse
from app.schemas.voice import (
    VoiceSynthesisRequest,
    VoiceSynthesisResponse,
    VoiceTranscriptionRequest,
    VoiceTranscriptionResponse,
)
from app.services.generation_admission import GenerationAdmissionRejectedError
from app.services.voice import (
    VoiceAssetNotFoundError,
    VoiceAssetUnsupportedError,
    VoiceIdempotencyConflictError,
    VoiceModelNotFoundError,
    VoiceModelUnavailableError,
    VoiceService,
)


router = APIRouter(prefix="/voice", tags=["Voice"])


def _service(request: Request, session: AsyncSession) -> VoiceService:
    storage = getattr(request.app.state, "asset_storage", None)
    catalog = getattr(request.app.state, "model_catalog", None)
    gpu_admission = getattr(
        request.app.state, "generation_admission_controller", None
    )
    if storage is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Attachment storage is unavailable",
        )
    if catalog is None or gpu_admission is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Local model catalog is unavailable",
        )
    return VoiceService(
        session,
        storage,
        catalog,
        recognition_runtime=getattr(
            request.app.state, "speech_recognition_runtime", None
        ),
        synthesis_runtime=getattr(
            request.app.state, "speech_synthesis_runtime", None
        ),
        gpu_admission_controller=gpu_admission,
    )


async def _cancel_on_disconnect(
    request: Request, operation: asyncio.Task[object]
) -> None:
    while True:
        message = await request.receive()
        if message["type"] != "http.disconnect":
            continue
        if not operation.done() and operation.cancelling() == 0:
            operation.cancel()
        return


async def _run_cancellable(request: Request, awaitable):
    operation = asyncio.current_task()
    if operation is None:  # pragma: no cover
        raise RuntimeError("voice operation task is unavailable")
    watcher = asyncio.create_task(
        _cancel_on_disconnect(request, operation),
        name="voice-client-disconnect-watcher",
    )
    try:
        return await awaitable
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)


def _translate_error(error: Exception) -> HTTPException:
    if isinstance(error, (VoiceAssetNotFoundError, VoiceModelNotFoundError)):
        return HTTPException(status_code=404, detail="Voice input or model not found")
    if isinstance(error, VoiceIdempotencyConflictError):
        return HTTPException(status_code=409, detail="Voice operation conflicts")
    if isinstance(error, (VoiceAssetUnsupportedError, SpeechRuntimeInputError)):
        return HTTPException(status_code=422, detail="Voice input is unsupported")
    if isinstance(error, GenerationAdmissionRejectedError):
        return HTTPException(status_code=429, detail="Local GPU capacity is busy")
    if isinstance(error, TimeoutError):
        return HTTPException(status_code=504, detail="Local voice operation timed out")
    return HTTPException(status_code=503, detail="Local voice runtime unavailable")


@router.post(
    "/transcriptions",
    response_model=VoiceTranscriptionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def transcribe_voice(
    body: VoiceTranscriptionRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> VoiceTranscriptionResponse:
    try:
        result = await _run_cancellable(
            request,
            _service(request, session).transcribe_for_owner(
                current_user.id, body.asset_id, body.model_id
            ),
        )
    except (
        VoiceAssetNotFoundError,
        VoiceAssetUnsupportedError,
        VoiceModelNotFoundError,
        VoiceModelUnavailableError,
        SpeechRuntimeInputError,
        SpeechRuntimeUnavailableError,
        GenerationAdmissionRejectedError,
        TimeoutError,
    ) as exc:
        raise _translate_error(exc) from None
    return VoiceTranscriptionResponse(
        text=result.text,
        language=result.language,
        duration_seconds=result.duration_seconds,
    )


@router.post(
    "/syntheses",
    response_model=VoiceSynthesisResponse,
    status_code=status.HTTP_201_CREATED,
)
async def synthesize_voice(
    body: VoiceSynthesisRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> VoiceSynthesisResponse:
    try:
        result = await _run_cancellable(
            request,
            _service(request, session).synthesize_for_owner(
                current_user.id,
                idempotency_key,
                body.model_id,
                body.text,
            ),
        )
    except (
        VoiceAssetUnsupportedError,
        VoiceIdempotencyConflictError,
        VoiceModelNotFoundError,
        VoiceModelUnavailableError,
        SpeechRuntimeInputError,
        SpeechRuntimeUnavailableError,
        TimeoutError,
    ) as exc:
        raise _translate_error(exc) from None
    response.status_code = (
        status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    )
    return VoiceSynthesisResponse(
        asset=AssetResponse.model_validate(result.asset),
        created=result.created,
    )
