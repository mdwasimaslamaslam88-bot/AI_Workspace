from uuid import UUID

from sqlalchemy import func, select, update

from app.models.user import User
from app.models.user_session import UserSession
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository):
    async def create(self, user: User) -> User:
        self.session.add(user)
        await self.session.flush()
        return user

    async def get_by_id(self, user_id: UUID) -> User | None:
        statement = select(User).where(User.id == user_id)
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_by_access_token_digest(
        self,
        access_token_digest: str,
    ) -> User | None:
        statement = (
            select(User, UserSession.id)
            .join(UserSession, UserSession.user_id == User.id)
            .where(
                UserSession.access_token_digest == access_token_digest,
                UserSession.revoked_at.is_(None),
            )
        )
        result = await self.session.execute(statement)
        row = result.one_or_none()
        if row is None:
            return None
        user, session_id = row
        user.bind_authenticated_session(session_id, access_token_digest)
        return user

    async def create_access_session(self, access_session: UserSession) -> UserSession:
        self.session.add(access_session)
        await self.session.flush()
        return access_session

    async def lock_owner_and_count_active_sessions(
        self,
        user_id: UUID,
    ) -> int | None:
        owner = await self.session.execute(
            select(User.id).where(User.id == user_id).with_for_update(of=User)
        )
        if owner.scalar_one_or_none() is None:
            return None
        count = await self.session.execute(
            select(func.count())
            .select_from(UserSession)
            .where(
                UserSession.user_id == user_id,
                UserSession.revoked_at.is_(None),
            )
        )
        return int(count.scalar_one())

    async def list_active_sessions_for_owner(
        self,
        user_id: UUID,
    ) -> tuple[UserSession, ...]:
        result = await self.session.execute(
            select(UserSession)
            .where(
                UserSession.user_id == user_id,
                UserSession.revoked_at.is_(None),
            )
            .order_by(UserSession.created_at.desc(), UserSession.id.desc())
        )
        return tuple(result.scalars().all())

    async def rename_active_session_for_owner(
        self,
        user_id: UUID,
        session_id: UUID,
        label: str | None,
    ) -> UserSession | None:
        result = await self.session.execute(
            update(UserSession)
            .where(
                UserSession.id == session_id,
                UserSession.user_id == user_id,
                UserSession.revoked_at.is_(None),
            )
            .values(label=label, updated_at=func.now())
            .returning(UserSession)
        )
        return result.scalar_one_or_none()

    async def revoke_active_session_for_owner(
        self,
        user_id: UUID,
        session_id: UUID,
    ) -> bool:
        result = await self.session.execute(
            update(UserSession)
            .where(
                UserSession.id == session_id,
                UserSession.user_id == user_id,
                UserSession.revoked_at.is_(None),
            )
            .values(revoked_at=func.now(), updated_at=func.now())
            .returning(UserSession.id)
        )
        return result.scalar_one_or_none() is not None

    async def rotate_access_token_digest(
        self,
        user_id: UUID,
        session_id: UUID,
        expected_access_token_digest: str,
        replacement_access_token_digest: str,
    ) -> bool:
        statement = (
            update(UserSession)
            .where(
                UserSession.id == session_id,
                UserSession.user_id == user_id,
                UserSession.access_token_digest == expected_access_token_digest,
                UserSession.revoked_at.is_(None),
            )
            .values(
                access_token_digest=replacement_access_token_digest,
                updated_at=func.now(),
            )
            .returning(UserSession.id)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none() is not None
