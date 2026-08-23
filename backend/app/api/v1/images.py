from __future__ import annotations

import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.db.dependencies import get_db_session
from app.images import ImageRuntimeInputError, ImageRuntimeUnavailableError
from app.models.user import User
from app.schemas.asset import AssetResponse
from app.schemas.image import (
    ImageEditingRequest,
    ImageGenerationRequest,
    ImageOperationResponse,
)
from app.schemas.message import MessageResponse
from app.services.generation_admission import GenerationAdmissionRejectedError
from app.services.image import (
    ImageOperationConflictError,
    ImageOperationNotFoundError,
    ImageOperationResult,
    ImageOperationUnavailableError,
    ImageService,
)


router = APIRouter(prefix="/images", tags=["Images"])


def _service(request: Request, session: AsyncSession) -> ImageService:
    storage = getattr(request.app.state, "asset_storage", None)
    catalog = getattr(request.app.state, "model_catalog", None)
    gpu_admission = getattr(
        request.app.state, "generation_admission_controller", None
    )
    if storage is None or catalog is None or gpu_admission is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Local image services are unavailable",
        )
    return ImageService(
        session,
        storage,
        catalog,
        generation_runtime=getattr(
            request.app.state, "image_generation_runtime", None
        ),
        editing_runtime=getattr(request.app.state, "image_editing_runtime", None),
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
        raise RuntimeError("image operation task is unavailable")
    watcher = asyncio.create_task(
        _cancel_on_disconnect(request, operation),
        name="image-client-disconnect-watcher",
    )
    try:
        return await awaitable
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)


def _translate_error(error: Exception) -> HTTPException:
    if isinstance(error, ImageOperationNotFoundError):
        return HTTPException(status_code=404, detail="Image input or model not found")
    if isinstance(error, ImageOperationConflictError):
        return HTTPException(status_code=409, detail="Image operation conflicts")
    if isinstance(error, ImageRuntimeInputError):
        return HTTPException(status_code=422, detail="Image input is unsupported")
    if isinstance(error, GenerationAdmissionRejectedError):
        return HTTPException(status_code=429, detail="Local GPU capacity is busy")
    if isinstance(error, TimeoutError):
        return HTTPException(status_code=504, detail="Local image operation timed out")
    return HTTPException(status_code=503, detail="Local image runtime unavailable")


def _response(result: ImageOperationResult) -> ImageOperationResponse:
    return ImageOperationResponse(
        asset=AssetResponse.model_validate(result.asset),
        message=MessageResponse.model_validate(result.message),
        created=result.created,
    )


@router.post(
    "/generations",
    response_model=ImageOperationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def generate_image(
    body: ImageGenerationRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ImageOperationResponse:
    try:
        result = await _run_cancellable(
            request,
            _service(request, session).generate_for_owner(
                current_user.id,
                idempotency_key,
                body.conversation_id,
                body.model_id,
                body.prompt,
                negative_prompt=body.negative_prompt,
                width=body.width,
                height=body.height,
                steps=body.steps,
                guidance=body.guidance,
                seed=body.seed,
            ),
        )
    except (
        ImageOperationConflictError,
        ImageOperationNotFoundError,
        ImageOperationUnavailableError,
        ImageRuntimeInputError,
        ImageRuntimeUnavailableError,
        GenerationAdmissionRejectedError,
        TimeoutError,
    ) as exc:
        raise _translate_error(exc) from None
    response.status_code = 201 if result.created else 200
    return _response(result)


@router.post(
    "/edits",
    response_model=ImageOperationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def edit_image(
    body: ImageEditingRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ImageOperationResponse:
    try:
        result = await _run_cancellable(
            request,
            _service(request, session).edit_for_owner(
                current_user.id,
                idempotency_key,
                body.conversation_id,
                body.model_id,
                body.source_asset_id,
                body.instruction,
                mask_asset_id=body.mask_asset_id,
                negative_prompt=body.negative_prompt,
                steps=body.steps,
                guidance=body.guidance,
                denoise=body.denoise,
                seed=body.seed,
            ),
        )
    except (
        ImageOperationConflictError,
        ImageOperationNotFoundError,
        ImageOperationUnavailableError,
        ImageRuntimeInputError,
        ImageRuntimeUnavailableError,
        GenerationAdmissionRejectedError,
        TimeoutError,
    ) as exc:
        raise _translate_error(exc) from None
    response.status_code = 201 if result.created else 200
    return _response(result)
