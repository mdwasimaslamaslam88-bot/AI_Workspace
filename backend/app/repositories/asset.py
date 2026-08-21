from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update

from app.models.asset import Asset
from app.repositories.base import BaseRepository


@dataclass(frozen=True, slots=True)
class AssetStorageState:
    active_keys: frozenset[str]
    deleted_keys: frozenset[str]


class AssetRepository(BaseRepository):
    async def create(
        self,
        *,
        asset_id: UUID,
        owner_id: UUID,
        original_filename: str | None,
        media_type: str,
        byte_size: int,
        content_sha256: str,
        storage_key: str,
        upload_idempotency_key: UUID,
    ) -> Asset:
        asset = Asset(
            id=asset_id,
            owner_id=owner_id,
            original_filename=original_filename,
            media_type=media_type,
            byte_size=byte_size,
            content_sha256=content_sha256,
            storage_key=storage_key,
            upload_idempotency_key=upload_idempotency_key,
        )
        self.session.add(asset)
        await self.session.flush()
        return asset

    async def get_by_idempotency_key_for_owner(
        self,
        owner_id: UUID,
        idempotency_key: UUID,
    ) -> Asset | None:
        statement = select(Asset).where(
            Asset.owner_id == owner_id,
            Asset.upload_idempotency_key == idempotency_key,
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_active_for_owner(
        self,
        owner_id: UUID,
        asset_id: UUID,
    ) -> Asset | None:
        statement = select(Asset).where(
            Asset.id == asset_id,
            Asset.owner_id == owner_id,
            Asset.deleted_at.is_(None),
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def soft_delete_for_owner(
        self,
        owner_id: UUID,
        asset_id: UUID,
    ) -> Asset | None:
        statement = (
            update(Asset)
            .where(
                Asset.id == asset_id,
                Asset.owner_id == owner_id,
            )
            .values(deleted_at=func.coalesce(Asset.deleted_at, func.now()))
            .returning(Asset)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def list_storage_state(self) -> AssetStorageState:
        result = await self.session.execute(
            select(Asset.storage_key, Asset.deleted_at)
        )
        active: set[str] = set()
        deleted: set[str] = set()
        for storage_key, deleted_at in result.all():
            target = deleted if isinstance(deleted_at, datetime) else active
            target.add(storage_key)
        return AssetStorageState(
            active_keys=frozenset(active),
            deleted_keys=frozenset(deleted),
        )
