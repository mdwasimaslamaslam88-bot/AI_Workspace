from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select

from app.models.connector import Connector, ConnectorExecution
from app.models.user import User
from app.repositories.base import BaseRepository


class ConnectorRepository(BaseRepository):
    async def lock_owner_and_count_connectors(self, owner_id: UUID) -> int | None:
        owner = await self.session.execute(
            select(User.id).where(User.id == owner_id).with_for_update(of=User)
        )
        if owner.scalar_one_or_none() is None:
            return None
        count = await self.session.execute(
            select(func.count())
            .select_from(Connector)
            .where(Connector.owner_id == owner_id)
        )
        return int(count.scalar_one())

    async def get_for_owner(self, owner_id: UUID, connector_id: UUID) -> Connector | None:
        result = await self.session.execute(
            select(Connector).where(
                Connector.id == connector_id,
                Connector.owner_id == owner_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_owner(
        self, owner_id: UUID, *, limit: int = 50
    ) -> tuple[Connector, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("connector list limit is invalid")
        result = await self.session.execute(
            select(Connector)
            .where(Connector.owner_id == owner_id)
            .order_by(Connector.created_at.desc(), Connector.id.desc())
            .limit(limit)
        )
        return tuple(result.scalars().all())

    async def list_executions_for_owner(
        self,
        owner_id: UUID,
        *,
        connector_id: UUID | None = None,
        limit: int = 50,
    ) -> tuple[ConnectorExecution, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("connector execution list limit is invalid")
        statement = select(ConnectorExecution).where(
            ConnectorExecution.owner_id == owner_id
        )
        if connector_id is not None:
            statement = statement.where(
                ConnectorExecution.connector_id == connector_id
            )
        result = await self.session.execute(
            statement.order_by(
                ConnectorExecution.started_at.desc(),
                ConnectorExecution.id.desc(),
            ).limit(limit)
        )
        return tuple(result.scalars().all())
