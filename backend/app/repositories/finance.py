from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.models.finance import (
    FinanceArtifact,
    FinanceWorkspace,
    MarketAlert,
    MarketAlertStatus,
    MarketAssetClass,
    MarketWatchItem,
    PaperOrder,
    PaperPosition,
)
from app.models.user import User
from app.repositories.base import BaseRepository


_WORKSPACE_LOADS = (
    selectinload(FinanceWorkspace.watch_items),
    selectinload(FinanceWorkspace.positions),
    selectinload(FinanceWorkspace.orders),
    selectinload(FinanceWorkspace.alerts),
    selectinload(FinanceWorkspace.artifacts),
)


class FinanceRepository(BaseRepository):
    async def lock_owner_and_count_workspaces(self, owner_id: UUID) -> int | None:
        owner = await self.session.execute(
            select(User.id).where(User.id == owner_id).with_for_update(of=User)
        )
        if owner.scalar_one_or_none() is None:
            return None
        count = await self.session.execute(
            select(func.count())
            .select_from(FinanceWorkspace)
            .where(FinanceWorkspace.owner_id == owner_id)
        )
        return int(count.scalar_one())

    async def get_workspace_for_owner(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        *,
        for_update: bool = False,
    ) -> FinanceWorkspace | None:
        statement = (
            select(FinanceWorkspace)
            .options(*_WORKSPACE_LOADS)
            .where(
                FinanceWorkspace.id == workspace_id,
                FinanceWorkspace.owner_id == owner_id,
            )
        )
        if for_update:
            statement = statement.with_for_update(of=FinanceWorkspace)
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def list_workspaces_for_owner(
        self, owner_id: UUID, *, limit: int = 10
    ) -> tuple[FinanceWorkspace, ...]:
        if not 1 <= limit <= 10:
            raise ValueError("finance workspace history limit is invalid")
        result = await self.session.execute(
            select(FinanceWorkspace)
            .options(*_WORKSPACE_LOADS)
            .where(FinanceWorkspace.owner_id == owner_id)
            .order_by(FinanceWorkspace.created_at.desc(), FinanceWorkspace.id.desc())
            .limit(limit)
        )
        return tuple(result.scalars().unique().all())

    async def count_watch_items(self, workspace_id: UUID) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(MarketWatchItem)
            .where(MarketWatchItem.workspace_id == workspace_id)
        )
        return int(result.scalar_one())

    async def get_watch_item(
        self, owner_id: UUID, workspace_id: UUID, item_id: UUID
    ) -> MarketWatchItem | None:
        result = await self.session.execute(
            select(MarketWatchItem).where(
                MarketWatchItem.id == item_id,
                MarketWatchItem.workspace_id == workspace_id,
                MarketWatchItem.owner_id == owner_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_position(
        self,
        workspace_id: UUID,
        asset_class: MarketAssetClass,
        symbol: str,
        *,
        for_update: bool = False,
    ) -> PaperPosition | None:
        statement = select(PaperPosition).where(
            PaperPosition.workspace_id == workspace_id,
            PaperPosition.asset_class == asset_class,
            PaperPosition.symbol == symbol,
        )
        if for_update:
            statement = statement.with_for_update(of=PaperPosition)
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def count_orders(self, workspace_id: UUID) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(PaperOrder)
            .where(PaperOrder.workspace_id == workspace_id)
        )
        return int(result.scalar_one())

    async def count_alerts(self, workspace_id: UUID) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(MarketAlert)
            .where(MarketAlert.workspace_id == workspace_id)
        )
        return int(result.scalar_one())

    async def active_alerts_for_quote(
        self,
        workspace_id: UUID,
        asset_class: MarketAssetClass,
        symbol: str,
    ) -> tuple[MarketAlert, ...]:
        result = await self.session.execute(
            select(MarketAlert)
            .where(
                MarketAlert.workspace_id == workspace_id,
                MarketAlert.asset_class == asset_class,
                MarketAlert.symbol == symbol,
                MarketAlert.status == MarketAlertStatus.ACTIVE,
            )
            .with_for_update(of=MarketAlert)
        )
        return tuple(result.scalars().all())

    async def count_artifacts(self, workspace_id: UUID) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(FinanceArtifact)
            .where(FinanceArtifact.workspace_id == workspace_id)
        )
        return int(result.scalar_one())
