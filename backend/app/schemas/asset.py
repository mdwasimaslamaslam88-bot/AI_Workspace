from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.asset import AssetProvenanceKind


class AssetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    original_filename: str | None
    media_type: str
    byte_size: int
    content_sha256: str
    provenance_kind: AssetProvenanceKind
    source_asset_id: UUID | None
    runtime_id: str | None
    model_id: str | None
    created_at: datetime
    deleted_at: datetime | None
