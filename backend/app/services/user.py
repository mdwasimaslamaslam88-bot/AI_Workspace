from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import digest_access_token, generate_access_token
from app.models.user import User
from app.models.user_session import (
    MAX_USER_SESSION_LABEL_CHARACTERS,
    UserSession,
)
from app.repositories.user import UserRepository


MAX_ACTIVE_USER_SESSIONS = 16


class UserSessionLimitError(RuntimeError):
    """The bounded active owner-session limit has been reached."""


def _validate_session_label(label: str | None) -> None:
    if label is None:
        return
    if not isinstance(label, str):
        raise TypeError("session label must be a string")
    if not 1 <= len(label.strip()) <= MAX_USER_SESSION_LABEL_CHARACTERS:
        raise ValueError("session label is invalid")


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
            access_token_digest = digest_access_token(access_token)
            user = User(access_token_digest=None)
            created = await self.repository.create(user)
            access_session = await self.repository.create_access_session(
                UserSession(
                    id=uuid4(),
                    user_id=created.id,
                    access_token_digest=access_token_digest,
                    label="Provisioned owner session",
                )
            )
            await self.session.commit()
            created.bind_authenticated_session(
                access_session.id,
                access_token_digest,
            )
            return created, access_token
        except BaseException:
            await self.session.rollback()
            raise

    async def rotate_access_token(
        self,
        user_id: UUID,
        session_id: UUID,
        expected_access_token_digest: str,
    ) -> str | None:
        try:
            access_token = generate_access_token()
            replacement_digest = digest_access_token(access_token)
            rotated = await self.repository.rotate_access_token_digest(
                user_id,
                session_id,
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

    async def create_access_session_for_owner(
        self,
        user_id: UUID,
        label: str | None,
    ) -> tuple[UserSession, str] | None:
        _validate_session_label(label)
        try:
            active_count = await self.repository.lock_owner_and_count_active_sessions(
                user_id
            )
            if active_count is None:
                await self.session.rollback()
                return None
            if active_count >= MAX_ACTIVE_USER_SESSIONS:
                raise UserSessionLimitError("active session limit reached")
            access_token = generate_access_token()
            access_session = await self.repository.create_access_session(
                UserSession(
                    id=uuid4(),
                    user_id=user_id,
                    access_token_digest=digest_access_token(access_token),
                    label=label,
                )
            )
            await self.session.commit()
            return access_session, access_token
        except BaseException:
            await self.session.rollback()
            raise

    async def list_active_sessions_for_owner(
        self,
        user_id: UUID,
    ) -> tuple[UserSession, ...]:
        try:
            return await self.repository.list_active_sessions_for_owner(user_id)
        except BaseException:
            await self.session.rollback()
            raise

    async def rename_active_session_for_owner(
        self,
        user_id: UUID,
        session_id: UUID,
        label: str | None,
    ) -> UserSession | None:
        _validate_session_label(label)
        try:
            access_session = await self.repository.rename_active_session_for_owner(
                user_id,
                session_id,
                label,
            )
            if access_session is None:
                await self.session.rollback()
                return None
            await self.session.commit()
            return access_session
        except BaseException:
            await self.session.rollback()
            raise

    async def revoke_active_session_for_owner(
        self,
        user_id: UUID,
        session_id: UUID,
    ) -> bool:
        try:
            revoked = await self.repository.revoke_active_session_for_owner(
                user_id,
                session_id,
            )
            if not revoked:
                await self.session.rollback()
                return False
            await self.session.commit()
            return True
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
