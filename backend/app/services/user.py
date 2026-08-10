from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.repositories.user import UserRepository


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = UserRepository(session)

    async def create(self, user: User) -> User:
        try:
            created = await self.repository.create(user)
            await self.session.commit()
            return created
        except BaseException:
            await self.session.rollback()
            raise

    async def get_by_id(self, user_id: UUID) -> User | None:
        try:
            return await self.repository.get_by_id(user_id)
        except BaseException:
            await self.session.rollback()
            raise
