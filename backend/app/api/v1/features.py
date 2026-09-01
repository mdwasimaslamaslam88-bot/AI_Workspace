from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_current_user
from app.features import FEATURE_REGISTRY
from app.models.user import User
from app.schemas.features import FeatureRegistryResponse, FeatureResponse


router = APIRouter(prefix="/features", tags=["Features"])


@router.get("", response_model=FeatureRegistryResponse)
async def list_features(
    _current_user: Annotated[User, Depends(get_current_user)],
) -> FeatureRegistryResponse:
    items = [FeatureResponse.model_validate(record, from_attributes=True) for record in FEATURE_REGISTRY]
    return FeatureRegistryResponse(count=len(items), items=items)
