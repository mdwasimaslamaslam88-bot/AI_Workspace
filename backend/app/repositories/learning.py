from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.models.asset import Asset
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.models.learning import (
    LearningActivity,
    LearningAttempt,
    LearningEvent,
    LearningLesson,
    LearningProgram,
    LearningReviewItem,
    LearningSession,
    LearningSessionStatus,
    LearningSkill,
    LearningSource,
)
from app.models.user import User
from app.repositories.base import BaseRepository


_PROGRAM_LOADS = (
    selectinload(LearningProgram.lessons).selectinload(LearningLesson.activities).selectinload(
        LearningActivity.attempts
    ),
    selectinload(LearningProgram.review_items),
    selectinload(LearningProgram.skills),
    selectinload(LearningProgram.sources),
)


@dataclass(frozen=True, slots=True)
class LearningSourceSnapshot:
    document_id: UUID
    asset_id: UUID
    label: str
    source_sha256: str


@dataclass(frozen=True, slots=True)
class LearningSourceChunk:
    source_id: UUID
    asset_id: UUID
    label: str
    content: str
    ordinal: int
    page_number: int | None
    row_start: int | None
    row_end: int | None
    section: str | None


class LearningRepository(BaseRepository):
    async def lock_owner_and_count_programs(self, owner_id: UUID) -> int | None:
        owner = await self.session.execute(
            select(User.id).where(User.id == owner_id).with_for_update(of=User)
        )
        if owner.scalar_one_or_none() is None:
            return None
        count = await self.session.execute(
            select(func.count())
            .select_from(LearningProgram)
            .where(LearningProgram.owner_id == owner_id)
        )
        return int(count.scalar_one())

    async def get_program_for_owner(
        self,
        owner_id: UUID,
        program_id: UUID,
        *,
        for_update: bool = False,
    ) -> LearningProgram | None:
        statement = (
            select(LearningProgram)
            .options(*_PROGRAM_LOADS)
            .where(
                LearningProgram.id == program_id,
                LearningProgram.owner_id == owner_id,
            )
        )
        if for_update:
            statement = statement.with_for_update(of=LearningProgram)
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def list_programs_for_owner(
        self, owner_id: UUID, *, limit: int = 20
    ) -> tuple[LearningProgram, ...]:
        if not 1 <= limit <= 20:
            raise ValueError("learning program history limit is invalid")
        result = await self.session.execute(
            select(LearningProgram)
            .options(*_PROGRAM_LOADS)
            .where(LearningProgram.owner_id == owner_id)
            .order_by(LearningProgram.updated_at.desc(), LearningProgram.id.desc())
            .limit(limit)
        )
        return tuple(result.scalars().unique().all())

    async def get_lesson_for_owner(
        self,
        owner_id: UUID,
        program_id: UUID,
        lesson_id: UUID,
        *,
        for_update: bool = False,
    ) -> LearningLesson | None:
        statement = (
            select(LearningLesson)
            .options(selectinload(LearningLesson.activities).selectinload(LearningActivity.attempts))
            .where(
                LearningLesson.id == lesson_id,
                LearningLesson.program_id == program_id,
                LearningLesson.owner_id == owner_id,
            )
        )
        if for_update:
            statement = statement.with_for_update(of=LearningLesson)
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_activity_for_owner(
        self,
        owner_id: UUID,
        program_id: UUID,
        activity_id: UUID,
        *,
        for_update: bool = False,
    ) -> LearningActivity | None:
        statement = (
            select(LearningActivity)
            .options(selectinload(LearningActivity.attempts))
            .where(
                LearningActivity.id == activity_id,
                LearningActivity.program_id == program_id,
                LearningActivity.owner_id == owner_id,
            )
        )
        if for_update:
            statement = statement.with_for_update(of=LearningActivity)
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_review_item_for_owner(
        self,
        owner_id: UUID,
        program_id: UUID,
        item_id: UUID,
        *,
        for_update: bool = False,
    ) -> LearningReviewItem | None:
        statement = select(LearningReviewItem).where(
            LearningReviewItem.id == item_id,
            LearningReviewItem.program_id == program_id,
            LearningReviewItem.owner_id == owner_id,
        )
        if for_update:
            statement = statement.with_for_update(of=LearningReviewItem)
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def recent_attempt_scores(
        self, owner_id: UUID, program_id: UUID, *, limit: int = 3
    ) -> tuple[int, ...]:
        result = await self.session.execute(
            select(LearningAttempt.score_bps)
            .where(
                LearningAttempt.owner_id == owner_id,
                LearningAttempt.program_id == program_id,
            )
            .order_by(LearningAttempt.created_at.desc(), LearningAttempt.id.desc())
            .limit(limit)
        )
        return tuple(int(value) for value in result.scalars().all())

    async def get_source_snapshot_for_owner(
        self, owner_id: UUID, document_id: UUID
    ) -> LearningSourceSnapshot | None:
        result = await self.session.execute(
            select(
                Document.id,
                Asset.id,
                Asset.original_filename,
                Asset.content_sha256,
            )
            .join(Asset, Asset.id == Document.asset_id)
            .where(
                Document.id == document_id,
                Document.owner_id == owner_id,
                Document.status == DocumentStatus.READY,
                Asset.owner_id == owner_id,
                Asset.deleted_at.is_(None),
            )
        )
        row = result.one_or_none()
        if row is None:
            return None
        return LearningSourceSnapshot(
            document_id=row[0],
            asset_id=row[1],
            label=(row[2] or f"Document {str(row[1])[:8]}")[:255],
            source_sha256=row[3],
        )

    async def list_source_chunks(
        self, owner_id: UUID, program_id: UUID
    ) -> tuple[LearningSourceChunk, ...]:
        result = await self.session.execute(
            select(
                LearningSource.id,
                LearningSource.asset_id,
                LearningSource.label,
                DocumentChunk.content,
                DocumentChunk.ordinal,
                DocumentChunk.page_number,
                DocumentChunk.row_start,
                DocumentChunk.row_end,
                DocumentChunk.section,
            )
            .join(
                Document,
                (Document.id == LearningSource.document_id)
                & (Document.owner_id == LearningSource.owner_id),
            )
            .join(
                DocumentChunk,
                (DocumentChunk.document_id == LearningSource.document_id)
                & (DocumentChunk.owner_id == LearningSource.owner_id),
            )
            .join(
                Asset,
                (Asset.id == LearningSource.asset_id)
                & (Asset.owner_id == LearningSource.owner_id),
            )
            .where(
                LearningSource.owner_id == owner_id,
                LearningSource.program_id == program_id,
                Document.status == DocumentStatus.READY,
                Asset.deleted_at.is_(None),
                Asset.content_sha256 == LearningSource.source_sha256,
            )
            .order_by(LearningSource.created_at, DocumentChunk.ordinal)
            .limit(512)
        )
        return tuple(
            LearningSourceChunk(
                source_id=row[0],
                asset_id=row[1],
                label=row[2],
                content=row[3],
                ordinal=row[4],
                page_number=row[5],
                row_start=row[6],
                row_end=row[7],
                section=row[8],
            )
            for row in result.all()
        )

    async def get_skill_for_owner(
        self,
        owner_id: UUID,
        program_id: UUID,
        name: str,
        *,
        for_update: bool = False,
    ) -> LearningSkill | None:
        statement = select(LearningSkill).where(
            LearningSkill.owner_id == owner_id,
            LearningSkill.program_id == program_id,
            LearningSkill.name == name,
        )
        if for_update:
            statement = statement.with_for_update(of=LearningSkill)
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def count_sessions_for_program(self, owner_id: UUID, program_id: UUID) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(LearningSession)
            .where(
                LearningSession.owner_id == owner_id,
                LearningSession.program_id == program_id,
            )
        )
        return int(result.scalar_one())

    async def get_open_session(
        self, owner_id: UUID, program_id: UUID, *, for_update: bool = False
    ) -> LearningSession | None:
        statement = select(LearningSession).where(
            LearningSession.owner_id == owner_id,
            LearningSession.program_id == program_id,
            LearningSession.status.in_(
                (LearningSessionStatus.ACTIVE, LearningSessionStatus.PAUSED)
            ),
        )
        if for_update:
            statement = statement.with_for_update(of=LearningSession)
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_session_for_owner(
        self,
        owner_id: UUID,
        program_id: UUID,
        session_id: UUID,
        *,
        for_update: bool = False,
    ) -> LearningSession | None:
        statement = select(LearningSession).where(
            LearningSession.id == session_id,
            LearningSession.owner_id == owner_id,
            LearningSession.program_id == program_id,
        )
        if for_update:
            statement = statement.with_for_update(of=LearningSession)
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def list_events_for_owner(
        self, owner_id: UUID, program_id: UUID, *, limit: int = 100
    ) -> tuple[LearningEvent, ...]:
        result = await self.session.execute(
            select(LearningEvent)
            .where(
                LearningEvent.owner_id == owner_id,
                LearningEvent.program_id == program_id,
            )
            .order_by(LearningEvent.created_at.desc(), LearningEvent.id.desc())
            .limit(limit)
        )
        return tuple(result.scalars().all())
