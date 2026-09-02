from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.models.creative import CreativeExperience
from app.models.user import User
from app.repositories.base import BaseRepository


class CreativeExperienceRepository(BaseRepository):
    async def lock_owner_and_count(self, owner_id: UUID) -> int | None:
        owner = await self.session.execute(
            select(User.id).where(User.id == owner_id).with_for_update(of=User)
        )
        if owner.scalar_one_or_none() is None:
            return None
        result = await self.session.execute(
            select(func.count())
            .select_from(CreativeExperience)
            .where(CreativeExperience.owner_id == owner_id)
        )
        return int(result.scalar_one())

    async def get_for_owner(
        self,
        owner_id: UUID,
        experience_id: UUID,
        *,
        for_update: bool = False,
    ) -> CreativeExperience | None:
        statement = (
            select(CreativeExperience)
            .options(selectinload(CreativeExperience.turns))
            .where(
                CreativeExperience.id == experience_id,
                CreativeExperience.owner_id == owner_id,
            )
        )
        if for_update:
            statement = statement.with_for_update(of=CreativeExperience)
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def list_for_owner(
        self, owner_id: UUID, *, limit: int = 20
    ) -> tuple[CreativeExperience, ...]:
        if not 1 <= limit <= 20:
            raise ValueError("creative experience history limit is invalid")
        result = await self.session.execute(
            select(CreativeExperience)
            .options(selectinload(CreativeExperience.turns))
            .where(CreativeExperience.owner_id == owner_id)
            .order_by(CreativeExperience.updated_at.desc(), CreativeExperience.id.desc())
            .limit(limit)
        )
        return tuple(result.scalars().unique().all())
