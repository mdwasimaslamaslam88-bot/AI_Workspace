from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.ai.catalog import ModelRuntimeUnavailableError
from app.api.dependencies import get_current_user
from app.models.user import User
from app.schemas.ai import LocalModelPageResponse, LocalModelResponse


router = APIRouter(prefix="/ai", tags=["AI"])


@router.get("/models", response_model=LocalModelPageResponse)
async def list_local_models(
    request: Request,
    _current_user: Annotated[User, Depends(get_current_user)],
) -> LocalModelPageResponse:
    catalog = getattr(request.app.state, "model_catalog", None)
    if catalog is None:
        raise RuntimeError("Local model catalog is not configured")

    try:
        models = await catalog.list_models()
    except ModelRuntimeUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Local model runtime unavailable",
        ) from None

    return LocalModelPageResponse(
        items=[
            LocalModelResponse(
                model_id=model.model_id,
                display_name=model.display_name,
                runtime_id=model.runtime_id,
                modality=model.modality,
                family=model.family,
                parameter_class=model.parameter_class,
                capabilities=list(model.capabilities),
                context_window=model.context_window,
                quantization=model.quantization,
                estimated_vram_bytes=model.estimated_vram_bytes,
                availability=model.availability,
            )
            for model in models
        ]
    )
