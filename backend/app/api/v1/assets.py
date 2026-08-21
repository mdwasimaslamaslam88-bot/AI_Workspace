import asyncio
import errno
import os
import re
from collections.abc import AsyncIterator
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from python_multipart.exceptions import MultipartParseError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from app.api.dependencies import get_current_user
from app.core.config import settings
from app.db.dependencies import get_db_session
from app.models.user import User
from app.schemas.asset import AssetResponse
from app.services.asset import (
    ASSET_COPY_CHUNK_BYTES,
    AssetEmptyError,
    AssetFilenameInvalidError,
    AssetService,
)
from app.storage.base import AssetStorage
from app.storage.local import StorageError


router = APIRouter(prefix="/assets", tags=["Assets"])


def _storage(request: Request) -> AssetStorage:
    storage = getattr(request.app.state, "asset_storage", None)
    if storage is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Attachment storage is unavailable",
        )
    return storage


def _bad_upload() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Upload could not be accepted",
    )


async def _parse_multipart(request: Request):
    content_type = request.headers.get("Content-Type")
    if (
        content_type is None
        or content_type.split(";", 1)[0].strip().lower() != "multipart/form-data"
    ):
        raise MultiPartException("Expected multipart form data")
    parser = MultiPartParser(
        headers=request.headers,
        stream=request.stream(),
        max_files=2,
        max_fields=1,
        max_part_size=settings.REQUEST_MAX_BODY_BYTES,
    )
    try:
        return await parser.parse()
    except BaseException:
        # Starlette only closes partial files for its own parser exceptions;
        # python-multipart may raise a lower-level parse error instead.
        for temporary_file in parser._files_to_close_on_error:
            try:
                temporary_file.close()
            except OSError:
                pass
        raise


def _safe_content_disposition(filename: str | None) -> str:
    display = filename or "attachment"
    ascii_name = re.sub(r"[^A-Za-z0-9._-]", "_", display).strip(".")
    if not ascii_name:
        ascii_name = "attachment"
    encoded = quote(display, safe="")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"


async def _read_file(handle) -> AsyncIterator[bytes]:
    try:
        while True:
            chunk = await asyncio.to_thread(handle.read, ASSET_COPY_CHUNK_BYTES)
            if not chunk:
                return
            yield chunk
    finally:
        await asyncio.to_thread(handle.close)


@router.post("", response_model=AssetResponse, status_code=status.HTTP_201_CREATED)
async def upload_asset(
    request: Request,
    response: Response,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> AssetResponse:
    storage = _storage(request)
    owner_id = current_user.id
    service = AssetService(session, storage)
    existing = await service.get_by_idempotency_key_for_owner(
        owner_id,
        idempotency_key,
    )
    if existing is not None:
        result = AssetResponse.model_validate(existing)
        await session.rollback()
        response.status_code = status.HTTP_200_OK
        return result

    # Authentication and idempotency lookup have completed; do not retain their
    # transaction while Starlette consumes and spools the multipart request.
    await session.rollback()
    try:
        form = await _parse_multipart(request)
        try:
            parts = form.multi_items()
            if (
                len(parts) != 1
                or parts[0][0] != "file"
                or not isinstance(parts[0][1], UploadFile)
            ):
                raise _bad_upload()
            upload = parts[0][1]
            result = await service.upload_for_owner(
                owner_id,
                idempotency_key,
                filename=upload.filename,
                claimed_media_type=upload.content_type,
                stream=upload,
            )
        finally:
            await form.close()
    except HTTPException:
        raise
    except (
        MultiPartException,
        MultipartParseError,
        AssetEmptyError,
        AssetFilenameInvalidError,
    ):
        raise _bad_upload() from None
    except OSError as exc:
        if exc.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)}:
            raise HTTPException(
                status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
                detail="Attachment storage is unavailable",
            ) from None
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Attachment storage is unavailable",
        ) from None
    except StorageError:
        raise HTTPException(
            status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
            detail="Attachment storage is unavailable",
        ) from None

    response.status_code = (
        status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    )
    return AssetResponse.model_validate(result.asset)


@router.get("/{asset_id}/content")
async def download_asset(
    asset_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    range_header: Annotated[str | None, Header(alias="Range")] = None,
):
    storage = _storage(request)
    content = await AssetService(session, storage).get_content_for_owner(
        current_user.id,
        asset_id,
    )
    if content is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )
    if range_header is not None:
        raise HTTPException(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            detail="Range requests are not supported",
            headers={"Content-Range": f"bytes */{content.byte_size}"},
        )
    try:
        handle = await asyncio.to_thread(storage.open_read, content.storage_key)
        if os.fstat(handle.fileno()).st_size != content.byte_size:
            handle.close()
            raise StorageError("asset content size does not match metadata")
    except (StorageError, OSError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Attachment content is unavailable",
        ) from None
    return StreamingResponse(
        _read_file(handle),
        media_type="application/octet-stream",
        headers={
            "Content-Length": str(content.byte_size),
            "Content-Disposition": _safe_content_disposition(
                content.original_filename
            ),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
            "Accept-Ranges": "none",
        },
    )


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset(
    asset_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    storage = _storage(request)
    deleted = await AssetService(session, storage).delete_for_owner(
        current_user.id,
        asset_id,
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )
