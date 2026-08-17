from uuid import UUID

from sqlalchemy import select, update

from app.models.user import User
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
        statement = select(User).where(
            User.access_token_digest == access_token_digest
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def rotate_access_token_digest(
        self,
        user_id: UUID,
        expected_access_token_digest: str,
        replacement_access_token_digest: str,
    ) -> bool:
        statement = (
            update(User)
            .where(
                User.id == user_id,
                User.access_token_digest == expected_access_token_digest,
            )
            .values(access_token_digest=replacement_access_token_digest)
            .returning(User.id)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none() is not None
