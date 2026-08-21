from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Asset
from app.repositories.asset import AssetRepository
from app.services.asset import AssetEmptyError, AssetService
from app.storage.local import LocalAssetStorage


class BytesStream:
    def __init__(self, content: bytes):
        self.content = content

    async def read(self, _size=-1):
        content, self.content = self.content, b""
        return content


def _service(storage):
    service = AssetService(AsyncMock(spec=AsyncSession), storage)
    service.repository = AsyncMock(spec=AssetRepository)

    async def create(**values):
        values["id"] = values.pop("asset_id")
        return Asset(
            **values,
            created_at=datetime.now(timezone.utc),
            deleted_at=None,
        )

    service.repository.create.side_effect = create
    return service


@pytest.mark.asyncio
async def test_identical_content_uploads_have_distinct_asset_ids_and_storage_keys(tmp_path):
    storage = LocalAssetStorage(tmp_path / "assets")
    owner_id = uuid4()
    first_service = _service(storage)
    second_service = _service(storage)

    first = await first_service.upload_for_owner(
        owner_id,
        uuid4(),
        filename="same.txt",
        claimed_media_type="text/plain",
        stream=BytesStream(b"identical"),
    )
    second = await second_service.upload_for_owner(
        owner_id,
        uuid4(),
        filename="same.txt",
        claimed_media_type="text/plain",
        stream=BytesStream(b"identical"),
    )

    assert first.asset.id != second.asset.id
    assert first.asset.storage_key != second.asset.storage_key
    assert first.asset.content_sha256 == second.asset.content_sha256
    assert first.asset.byte_size == second.asset.byte_size


@pytest.mark.asyncio
async def test_zero_byte_upload_aborts_without_metadata_or_final_file(tmp_path):
    storage = LocalAssetStorage(tmp_path / "assets")
    service = _service(storage)

    with pytest.raises(AssetEmptyError):
        await service.upload_for_owner(
            uuid4(),
            uuid4(),
            filename="empty.txt",
            claimed_media_type="text/plain",
            stream=BytesStream(b""),
        )

    service.repository.create.assert_not_awaited()
    assert list(storage.staging_root.iterdir()) == []
    assert [path for path in storage.objects_root.rglob("*") if path.is_file()] == []
