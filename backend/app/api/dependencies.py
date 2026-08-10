from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import digest_access_token, is_access_token_format_valid
from app.db.dependencies import get_db_session
from app.models.user import User
from app.services.user import UserService


_AUTHENTICATION_ERROR_DETAIL = "Invalid authentication credentials"


def _authentication_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=_AUTHENTICATION_ERROR_DETAIL,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _extract_bearer_token(authorization: str | None) -> str | None:
    if authorization is None:
        return None

    scheme, separator, credential = authorization.partition(" ")
    if (
        scheme.lower() != "bearer"
        or separator != " "
        or not credential
        or credential.strip() != credential
        or not is_access_token_format_valid(credential)
    ):
        return None
    return credential


async def get_current_user(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    authorization: Annotated[
        str | None,
        Header(alias="Authorization"),
    ] = None,
) -> User:
    access_token = _extract_bearer_token(authorization)
    if access_token is None:
        raise _authentication_error()

    digest = digest_access_token(access_token)
    user = await UserService(session).get_by_access_token_digest(digest)
    if user is None:
        raise _authentication_error()
    return user
