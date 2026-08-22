from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory import Memory, MemoryCategory, MemorySetting
from app.repositories.base import BaseRepository


MAX_MEMORY_LIST_ITEMS = 200
MAX_MEMORY_RETRIEVAL_CANDIDATES = 500


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    id: UUID
    category: MemoryCategory
    content: str
    embedding: bytes
    created_at: datetime


class MemoryRepository(BaseRepository):
    async def create(
        self,
        owner_id: UUID,
        category: MemoryCategory,
        content: str,
        embedding: bytes,
        embedding_norm: float,
    ) -> Memory:
        memory = Memory(
            owner_id=owner_id,
            category=category,
            content=content,
            embedding=embedding,
            embedding_norm=embedding_norm,
            provenance_kind="explicit_user_entry",
        )
        self.session.add(memory)
        await self.session.flush()
        return memory

    async def get_for_owner(self, owner_id: UUID, memory_id: UUID) -> Memory | None:
        result = await self.session.execute(
            select(Memory).where(
                Memory.id == memory_id,
                Memory.owner_id == owner_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_owner(
        self,
        owner_id: UUID,
        *,
        include_deleted: bool,
        limit: int = MAX_MEMORY_LIST_ITEMS,
    ) -> tuple[Memory, ...]:
        statement = select(Memory).where(Memory.owner_id == owner_id)
        if not include_deleted:
            statement = statement.where(Memory.deleted_at.is_(None))
        result = await self.session.execute(
            statement.order_by(Memory.updated_at.desc(), Memory.id.desc()).limit(limit)
        )
        return tuple(result.scalars().all())

    async def forget_for_owner(self, owner_id: UUID, memory_id: UUID) -> Memory | None:
        result = await self.session.execute(
            update(Memory)
            .where(
                Memory.id == memory_id,
                Memory.owner_id == owner_id,
                Memory.deleted_at.is_(None),
            )
            .values(
                content=None,
                embedding=None,
                embedding_norm=None,
                deleted_at=func.now(),
                updated_at=func.now(),
            )
            .returning(Memory)
        )
        return result.scalar_one_or_none()

    async def setting_for_owner(self, owner_id: UUID) -> MemorySetting | None:
        return await self.session.get(MemorySetting, owner_id)

    async def update_setting_for_owner(
        self,
        owner_id: UUID,
        enabled: bool,
    ) -> MemorySetting | None:
        result = await self.session.execute(
            update(MemorySetting)
            .where(MemorySetting.owner_id == owner_id)
            .values(enabled=enabled, updated_at=func.now())
            .returning(MemorySetting)
        )
        return result.scalar_one_or_none()

    async def create_setting(self, owner_id: UUID, enabled: bool) -> MemorySetting:
        setting = MemorySetting(owner_id=owner_id, enabled=enabled)
        self.session.add(setting)
        await self.session.flush()
        return setting

    async def list_retrieval_candidates(
        self,
        owner_id: UUID,
        *,
        limit: int = MAX_MEMORY_RETRIEVAL_CANDIDATES,
    ) -> tuple[MemoryCandidate, ...]:
        result = await self.session.execute(
            select(
                Memory.id,
                Memory.category,
                Memory.content,
                Memory.embedding,
                Memory.created_at,
            )
            .where(
                Memory.owner_id == owner_id,
                Memory.deleted_at.is_(None),
                Memory.content.is_not(None),
                Memory.embedding.is_not(None),
            )
            .order_by(Memory.updated_at.desc(), Memory.id.desc())
            .limit(limit)
        )
        return tuple(
            MemoryCandidate(
                id=row[0],
                category=row[1],
                content=row[2],
                embedding=row[3],
                created_at=row[4],
            )
            for row in result.all()
        )
