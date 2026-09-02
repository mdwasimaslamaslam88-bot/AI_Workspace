from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.models.marketing import MarketingCampaign, MarketingCampaignStatus
from app.models.user import User
from app.repositories.base import BaseRepository


class MarketingCampaignRepository(BaseRepository):
    async def lock_owner_and_count_campaigns(self, owner_id: UUID) -> int | None:
        owner = await self.session.execute(
            select(User.id).where(User.id == owner_id).with_for_update(of=User)
        )
        if owner.scalar_one_or_none() is None:
            return None
        count = await self.session.execute(
            select(func.count())
            .select_from(MarketingCampaign)
            .where(MarketingCampaign.owner_id == owner_id)
        )
        return int(count.scalar_one())

    async def get_for_owner(
        self,
        owner_id: UUID,
        campaign_id: UUID,
        *,
        for_update: bool = False,
    ) -> MarketingCampaign | None:
        statement = (
            select(MarketingCampaign)
            .options(selectinload(MarketingCampaign.stages))
            .where(
                MarketingCampaign.id == campaign_id,
                MarketingCampaign.owner_id == owner_id,
            )
        )
        if for_update:
            statement = statement.with_for_update(of=MarketingCampaign)
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def list_for_owner(
        self,
        owner_id: UUID,
        *,
        limit: int = 20,
    ) -> tuple[MarketingCampaign, ...]:
        if not 1 <= limit <= 50:
            raise ValueError("marketing campaign history limit is invalid")
        result = await self.session.execute(
            select(MarketingCampaign)
            .options(selectinload(MarketingCampaign.stages))
            .where(MarketingCampaign.owner_id == owner_id)
            .order_by(
                MarketingCampaign.created_at.desc(),
                MarketingCampaign.id.desc(),
            )
            .limit(limit)
        )
        return tuple(result.scalars().unique().all())

    async def list_for_owner_global_interrupted(
        self,
    ) -> tuple[MarketingCampaign, ...]:
        result = await self.session.execute(
            select(MarketingCampaign)
            .options(selectinload(MarketingCampaign.stages))
            .where(
                MarketingCampaign.status.in_(
                    (
                        MarketingCampaignStatus.RUNNING,
                        MarketingCampaignStatus.PUBLISHING,
                    )
                )
            )
            .with_for_update(of=MarketingCampaign)
        )
        return tuple(result.scalars().unique().all())
