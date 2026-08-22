from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.documents.embedding import EmbeddingError, cosine_similarity, embed_text
from app.models.memory import MAX_MEMORY_CONTENT_CHARACTERS, Memory, MemoryCategory
from app.repositories.memory import MemoryCandidate, MemoryRepository


MAX_RETRIEVED_MEMORIES = 8
MAX_MEMORY_CONTEXT_CHARACTERS = 4_000
MIN_MEMORY_RELEVANCE_SCORE = 0.25


class MemoryContentInvalidError(ValueError):
    """Explicit memory content is blank, oversized, or not embeddable."""


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    id: UUID
    category: MemoryCategory
    content: str | None
    provenance_kind: str
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


@dataclass(frozen=True, slots=True)
class MemorySettingRecord:
    enabled: bool
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class RetrievedMemory:
    id: UUID
    category: MemoryCategory
    content: str
    score: float
    created_at: datetime

    def source_label(self, position: int) -> str:
        return f"[memory {position}: {self.category.value}]"


def _memory_record(memory: Memory) -> MemoryRecord:
    return MemoryRecord(
        id=memory.id,
        category=memory.category,
        content=memory.content,
        provenance_kind=memory.provenance_kind,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
        deleted_at=memory.deleted_at,
    )


def validate_memory_content(content: str) -> str:
    if not isinstance(content, str):
        raise TypeError("memory content must be text")
    if not content.strip() or len(content) > MAX_MEMORY_CONTENT_CHARACTERS:
        raise MemoryContentInvalidError("memory content is invalid")
    return content


class MemoryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = MemoryRepository(session)

    async def create_for_owner(
        self,
        owner_id: UUID,
        category: MemoryCategory,
        content: str,
    ) -> MemoryRecord:
        content = validate_memory_content(content)
        try:
            embedding = embed_text(content)
        except EmbeddingError as exc:
            raise MemoryContentInvalidError("memory content is invalid") from exc
        try:
            memory = await self.repository.create(
                owner_id,
                category,
                content,
                embedding.packed,
                embedding.norm,
            )
            await self.session.commit()
            return _memory_record(memory)
        except BaseException:
            await self.session.rollback()
            raise

    async def list_for_owner(
        self,
        owner_id: UUID,
        *,
        include_deleted: bool = True,
    ) -> tuple[MemoryRecord, ...]:
        try:
            memories = await self.repository.list_for_owner(
                owner_id,
                include_deleted=include_deleted,
            )
            records = tuple(_memory_record(memory) for memory in memories)
            await self.session.rollback()
            return records
        except BaseException:
            await self.session.rollback()
            raise

    async def forget_for_owner(
        self,
        owner_id: UUID,
        memory_id: UUID,
    ) -> MemoryRecord | None:
        try:
            existing = await self.repository.get_for_owner(owner_id, memory_id)
            if existing is None:
                await self.session.rollback()
                return None
            if existing.deleted_at is not None:
                record = _memory_record(existing)
                await self.session.rollback()
                return record
            forgotten = await self.repository.forget_for_owner(owner_id, memory_id)
            if forgotten is None:
                await self.session.rollback()
                current = await self.repository.get_for_owner(owner_id, memory_id)
                record = (
                    _memory_record(current)
                    if current is not None and current.deleted_at is not None
                    else None
                )
                await self.session.rollback()
                return record
            await self.session.commit()
            return _memory_record(forgotten)
        except BaseException:
            await self.session.rollback()
            raise

    async def setting_for_owner(self, owner_id: UUID) -> MemorySettingRecord:
        try:
            setting = await self.repository.setting_for_owner(owner_id)
            if setting is None:
                record = MemorySettingRecord(True, None, None)
            else:
                record = MemorySettingRecord(
                    setting.enabled,
                    setting.created_at,
                    setting.updated_at,
                )
            await self.session.rollback()
            return record
        except BaseException:
            await self.session.rollback()
            raise

    async def set_enabled_for_owner(
        self,
        owner_id: UUID,
        enabled: bool,
    ) -> MemorySettingRecord:
        if not isinstance(enabled, bool):
            raise TypeError("memory enabled setting must be boolean")
        try:
            setting = await self.repository.update_setting_for_owner(
                owner_id,
                enabled,
            )
            if setting is None:
                try:
                    setting = await self.repository.create_setting(owner_id, enabled)
                    await self.session.commit()
                except IntegrityError:
                    await self.session.rollback()
                    setting = await self.repository.update_setting_for_owner(
                        owner_id,
                        enabled,
                    )
                    if setting is None:
                        raise
                    await self.session.commit()
            else:
                await self.session.commit()
            return MemorySettingRecord(
                setting.enabled,
                setting.created_at,
                setting.updated_at,
            )
        except BaseException:
            await self.session.rollback()
            raise

    async def retrieve_for_owner(
        self,
        owner_id: UUID,
        query: str,
        *,
        limit: int = MAX_RETRIEVED_MEMORIES,
    ) -> tuple[RetrievedMemory, ...]:
        if not isinstance(query, str) or not query.strip():
            return ()
        if not 1 <= limit <= MAX_RETRIEVED_MEMORIES:
            raise ValueError("memory retrieval limit is outside its bound")
        try:
            query_embedding = embed_text(query)
        except EmbeddingError:
            return ()
        try:
            setting = await self.repository.setting_for_owner(owner_id)
            if setting is not None and not setting.enabled:
                await self.session.rollback()
                return ()
            candidates = await self.repository.list_retrieval_candidates(owner_id)
            await self.session.rollback()
        except BaseException:
            await self.session.rollback()
            raise
        return self._select(query_embedding.packed, candidates, limit)

    @staticmethod
    def _select(
        query_embedding: bytes,
        candidates: tuple[MemoryCandidate, ...],
        limit: int,
    ) -> tuple[RetrievedMemory, ...]:
        scored: list[tuple[float, int, MemoryCandidate]] = []
        for candidate in candidates:
            try:
                similarity = cosine_similarity(query_embedding, candidate.embedding)
            except EmbeddingError:
                continue
            globally_applicable = candidate.category in {
                MemoryCategory.INSTRUCTION,
                MemoryCategory.PREFERENCE,
            }
            if (
                similarity < MIN_MEMORY_RELEVANCE_SCORE
                and not globally_applicable
            ):
                continue
            score = max(similarity, 0.15 if globally_applicable else 0.0)
            category_priority = (
                0 if candidate.category is MemoryCategory.INSTRUCTION else 1
            )
            scored.append((score, category_priority, candidate))
        scored.sort(key=lambda item: (-item[0], item[1], str(item[2].id)))
        selected: list[RetrievedMemory] = []
        character_count = 0
        for score, _priority, candidate in scored:
            if len(candidate.content) > MAX_MEMORY_CONTEXT_CHARACTERS - character_count:
                continue
            selected.append(
                RetrievedMemory(
                    id=candidate.id,
                    category=candidate.category,
                    content=candidate.content,
                    score=score,
                    created_at=candidate.created_at,
                )
            )
            character_count += len(candidate.content)
            if len(selected) >= limit:
                break
        return tuple(selected)
