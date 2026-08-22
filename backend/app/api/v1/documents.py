import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.db.dependencies import get_db_session
from app.models.user import User
from app.schemas.document import (
    DocumentPageResponse,
    DocumentResponse,
    DocumentSearchQuery,
    DocumentSearchResponse,
    DocumentSearchResultResponse,
)
from app.services.document import (
    DocumentContentUnavailableError,
    DocumentIngestionRejectedError,
    DocumentIngestionUnavailableError,
    DocumentNotFoundError,
    DocumentService,
    DocumentUnsupportedError,
)


router = APIRouter(prefix="/documents", tags=["Documents"])


def _document_service(request: Request, session: AsyncSession) -> DocumentService:
    admission = getattr(request.app.state, "document_ingestion_admission", None)
    max_duration = getattr(
        request.app.state,
        "document_ingestion_max_duration_seconds",
        None,
    )
    if admission is None or max_duration is None:
        raise RuntimeError("Document ingestion is not configured")
    return DocumentService(
        session,
        getattr(request.app.state, "asset_storage", None),
        admission,
        max_duration_seconds=max_duration,
        active_tasks=getattr(request.app.state, "document_ingestion_tasks", None),
    )


async def _cancel_on_disconnect(
    request: Request,
    task: asyncio.Task[object],
) -> None:
    while True:
        message = await request.receive()
        if message["type"] != "http.disconnect":
            continue
        if not task.done() and task.cancelling() == 0:
            task.cancel()
        return


@router.get("", response_model=DocumentPageResponse)
async def list_documents(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> DocumentPageResponse:
    documents = await _document_service(request, session).list_for_owner(
        current_user.id
    )
    return DocumentPageResponse(
        items=[DocumentResponse.model_validate(item) for item in documents]
    )


@router.get("/search", response_model=DocumentSearchResponse)
async def search_documents(
    query: Annotated[DocumentSearchQuery, Query()],
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> DocumentSearchResponse:
    try:
        items = await _document_service(request, session).search_for_owner(
            current_user.id,
            query.query,
            limit=query.limit,
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Document search query is invalid",
        ) from None
    return DocumentSearchResponse(
        items=[
            DocumentSearchResultResponse(
                chunk_id=item.chunk_id,
                asset_id=item.asset_id,
                content=item.content,
                score=item.score,
                original_filename=item.original_filename,
                provenance_kind=item.provenance_kind,
                page_number=item.page_number,
                row_start=item.row_start,
                row_end=item.row_end,
                section=item.section,
            )
            for item in items
        ]
    )


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> DocumentResponse:
    document = await _document_service(request, session).get_for_owner(
        current_user.id,
        document_id,
    )
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )
    return DocumentResponse.model_validate(document)


@router.post("/assets/{asset_id}/ingest", response_model=DocumentResponse)
async def ingest_document(
    asset_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> DocumentResponse:
    task = asyncio.current_task()
    if task is None:  # pragma: no cover
        raise RuntimeError("Document ingestion request task is unavailable")
    watcher = asyncio.create_task(
        _cancel_on_disconnect(request, task),
        name="document-ingestion-client-disconnect-watcher",
    )
    try:
        document = await _document_service(request, session).ingest_for_owner(
            current_user.id,
            asset_id,
        )
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document asset not found",
        ) from None
    except DocumentUnsupportedError:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Attachment is not a supported document",
        ) from None
    except DocumentIngestionRejectedError as exc:
        response_status = (
            status.HTTP_413_CONTENT_TOO_LARGE
            if exc.failure_code == "document_too_large"
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(
            status_code=response_status,
            detail="Document could not be ingested",
        ) from None
    except (
        DocumentContentUnavailableError,
        DocumentIngestionUnavailableError,
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Document ingestion is unavailable",
        ) from None
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
    return DocumentResponse.model_validate(document)


@router.delete(
    "/{document_id}/ingestion",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def cancel_document_ingestion(
    document_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    found = await _document_service(request, session).cancel_for_owner(
        current_user.id,
        document_id,
    )
    if not found:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )
