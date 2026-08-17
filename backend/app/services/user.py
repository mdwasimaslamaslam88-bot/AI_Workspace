from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import digest_access_token, generate_access_token
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

    async def provision_with_access_token(self) -> tuple[User, str]:
        try:
            access_token = generate_access_token()
            user = User(access_token_digest=digest_access_token(access_token))
            created = await self.repository.create(user)
            await self.session.commit()
            return created, access_token
        except BaseException:
            await self.session.rollback()
            raise

    async def rotate_access_token(
        self,
        user_id: UUID,
        expected_access_token_digest: str,
    ) -> str | None:
        try:
            access_token = generate_access_token()
            replacement_digest = digest_access_token(access_token)
            rotated = await self.repository.rotate_access_token_digest(
                user_id,
                expected_access_token_digest,
                replacement_digest,
            )
            if not rotated:
                await self.session.rollback()
                return None
            await self.session.commit()
            return access_token
        except BaseException:
            await self.session.rollback()
            raise

    async def get_by_id(self, user_id: UUID) -> User | None:
        try:
            return await self.repository.get_by_id(user_id)
        except BaseException:
            await self.session.rollback()
            raise

    async def get_by_access_token_digest(
        self,
        access_token_digest: str,
    ) -> User | None:
        try:
            return await self.repository.get_by_access_token_digest(
                access_token_digest
            )
        except BaseException:
            await self.session.rollback()
            raise
