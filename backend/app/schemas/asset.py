from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AssetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    original_filename: str | None
    media_type: str
    byte_size: int
    content_sha256: str
    created_at: datetime
    deleted_at: datetime | None
