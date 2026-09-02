from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.models.learning import (
    LearningActivity,
    LearningAttempt,
    LearningLesson,
    LearningProgram,
    LearningReviewItem,
)
from app.models.user import User
from app.repositories.base import BaseRepository


_PROGRAM_LOADS = (
    selectinload(LearningProgram.lessons).selectinload(LearningLesson.activities).selectinload(
        LearningActivity.attempts
    ),
    selectinload(LearningProgram.review_items),
)


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
