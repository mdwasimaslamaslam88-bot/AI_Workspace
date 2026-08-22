from __future__ import annotations

import asyncio
import hashlib
import hmac
import multiprocessing
import os
import stat
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO, Callable, MutableMapping, TypeVar
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.documents.embedding import EmbeddingError, cosine_similarity, embed_text
from app.documents.parsers import (
    DocumentChunkDraft,
    DocumentParseError,
    DocumentTooLargeError,
    MAX_DOCUMENT_BYTES,
    SUPPORTED_DOCUMENT_MEDIA_TYPES,
    chunk_document,
    parse_document,
)
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.repositories.document import (
    DocumentAssetSnapshot,
    DocumentRepository,
    RetrievalCandidate,
)
from app.storage.base import AssetStorage


DOCUMENT_READ_CHUNK_BYTES = 65_536
DOCUMENT_PARSER_MAX_SECONDS = 20.0
MAX_RETRIEVAL_RESULTS = 4
MAX_RETRIEVAL_CONTEXT_CHARACTERS = 6_000
_T = TypeVar("_T")


class DocumentNotFoundError(RuntimeError):
    """The owner cannot access the requested active asset or document."""


class DocumentUnsupportedError(RuntimeError):
    """The active asset is not a supported document media type."""


class DocumentContentUnavailableError(RuntimeError):
    """Owned document bytes cannot be read and verified."""


class DocumentIngestionRejectedError(RuntimeError):
    """Document parsing safely rejected the uploaded content."""

    def __init__(self, failure_code: str) -> None:
        super().__init__("document could not be ingested")
        self.failure_code = failure_code


class DocumentIngestionUnavailableError(RuntimeError):
    """The local ingestion runtime failed or exceeded its deadline."""


class _WorkCancelled(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class IndexedChunk:
    draft: DocumentChunkDraft
    embedding: bytes
    embedding_norm: float


@dataclass(frozen=True, slots=True)
class RetrievedDocumentChunk:
    chunk_id: UUID
    asset_id: UUID
    content: str
    score: float
    original_filename: str | None
    provenance_kind: str
    page_number: int | None
    row_start: int | None
    row_end: int | None
    section: str | None

    def source_label(self, position: int) -> str:
        label = (
            self.original_filename or f"Document {str(self.asset_id)[:8]}"
        )[:255]
        provenance: list[str] = []
        if self.page_number is not None:
            provenance.append(f"page {self.page_number}")
        if self.row_start is not None:
            row = (
                str(self.row_start)
                if self.row_end in (None, self.row_start)
                else f"{self.row_start}-{self.row_end}"
            )
            provenance.append(f"row {row}")
        if self.section:
            provenance.append(f"section {self.section}")
        suffix = f"; {', '.join(provenance)}" if provenance else ""
        return f"[source {position}: {label}{suffix}]"


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    id: UUID
    asset_id: UUID
    status: DocumentStatus
    source_state: str
    original_filename: str | None
    media_type: str | None
    chunk_count: int
    character_count: int
    failure_code: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


def _document_record(document: Document) -> DocumentRecord:
    deleted = document.asset.deleted_at is not None
    return DocumentRecord(
        id=document.id,
        asset_id=document.asset_id,
        status=document.status,
        source_state="deleted" if deleted else "active",
        original_filename=None if deleted else document.asset.original_filename,
        media_type=None if deleted else document.asset.media_type,
        chunk_count=document.chunk_count,
        character_count=document.character_count,
        failure_code=document.failure_code,
        created_at=document.created_at,
        updated_at=document.updated_at,
        completed_at=document.completed_at,
    )


def _parser_worker(
    connection,
    data: bytes,
    media_type: str,
) -> None:
    try:
        drafts = chunk_document(parse_document(data, media_type))
        connection.send((True, drafts))
    except DocumentTooLargeError:
        connection.send((False, "document_too_large"))
    except DocumentParseError:
        connection.send((False, "malformed_document"))
    except BaseException:
        connection.send((False, "parser_unavailable"))
    finally:
        connection.close()


def _run_parser_process(
    cancel_event: threading.Event,
    data: bytes,
    media_type: str,
) -> tuple[DocumentChunkDraft, ...]:
    context = multiprocessing.get_context("spawn")
    parent_connection, child_connection = context.Pipe(duplex=False)
    process = context.Process(
        target=_parser_worker,
        args=(child_connection, data, media_type),
        name="document-parser",
        daemon=True,
    )
    deadline = time.monotonic() + DOCUMENT_PARSER_MAX_SECONDS
    try:
        process.start()
        child_connection.close()
        while True:
            if cancel_event.is_set():
                raise _WorkCancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            if parent_connection.poll(min(0.05, remaining)):
                try:
                    succeeded, payload = parent_connection.recv()
                except EOFError as exc:
                    raise DocumentIngestionUnavailableError(
                        "local document parser is unavailable"
                    ) from exc
                if succeeded:
                    return payload
                if payload in {"document_too_large", "malformed_document"}:
                    raise DocumentIngestionRejectedError(payload)
                raise DocumentIngestionUnavailableError(
                    "local document parser is unavailable"
                )
            if not process.is_alive():
                raise DocumentIngestionUnavailableError(
                    "local document parser is unavailable"
                )
    finally:
        parent_connection.close()
        child_connection.close()
        if process.is_alive():
            process.terminate()
        process.join(timeout=1.0)
        if process.is_alive():
            process.kill()
            process.join(timeout=1.0)
        process.close()


def _read_verified_document(
    cancel_event: threading.Event,
    storage: AssetStorage,
    snapshot: DocumentAssetSnapshot,
) -> bytes:
    if snapshot.byte_size < 1 or snapshot.byte_size > MAX_DOCUMENT_BYTES:
        raise DocumentIngestionRejectedError("document_too_large")
    handle: BinaryIO | None = None
    content = bytearray()
    digest = hashlib.sha256()
    try:
        handle = storage.open_read(snapshot.storage_key)
        details = os.fstat(handle.fileno())
        if not stat.S_ISREG(details.st_mode) or details.st_size != snapshot.byte_size:
            raise DocumentContentUnavailableError(
                "document content is unavailable"
            )
        while True:
            if cancel_event.is_set():
                raise _WorkCancelled()
            chunk = handle.read(DOCUMENT_READ_CHUNK_BYTES)
            if not chunk:
                break
            if len(chunk) > snapshot.byte_size - len(content):
                raise DocumentContentUnavailableError(
                    "document content is unavailable"
                )
            digest.update(chunk)
            content.extend(chunk)
        if (
            len(content) != snapshot.byte_size
            or not hmac.compare_digest(digest.hexdigest(), snapshot.content_sha256)
        ):
            raise DocumentContentUnavailableError(
                "document content is unavailable"
            )
        return bytes(content)
    except (
        DocumentContentUnavailableError,
        DocumentIngestionRejectedError,
        _WorkCancelled,
    ):
        raise
    except Exception as exc:
        raise DocumentContentUnavailableError(
            "document content is unavailable"
        ) from exc
    finally:
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass
        content.clear()


def _embed_chunks(
    cancel_event: threading.Event,
    drafts: tuple[DocumentChunkDraft, ...],
) -> tuple[IndexedChunk, ...]:
    indexed: list[IndexedChunk] = []
    for draft in drafts:
        if cancel_event.is_set():
            raise _WorkCancelled()
        embedding = embed_text(draft.content)
        indexed.append(
            IndexedChunk(
                draft=draft,
                embedding=embedding.packed,
                embedding_norm=embedding.norm,
            )
        )
    return tuple(indexed)


async def _finish_task(task: asyncio.Task[_T]) -> _T:
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


async def _cancellable_thread(
    function: Callable[[threading.Event], _T],
) -> _T:
    cancel_event = threading.Event()
    task = asyncio.create_task(asyncio.to_thread(function, cancel_event))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        cancel_event.set()
        try:
            await _finish_task(task)
        except Exception:
            pass
        raise


class DocumentService:
    def __init__(
        self,
        session: AsyncSession,
        storage: AssetStorage | None,
        admission: asyncio.Semaphore | None,
        *,
        max_duration_seconds: float = 30.0,
        active_tasks: MutableMapping[
            tuple[UUID, UUID], asyncio.Task[object]
        ] | None = None,
    ) -> None:
        self.session = session
        self.storage = storage
        self.admission = admission
        self.max_duration_seconds = max_duration_seconds
        self.active_tasks = active_tasks
        self.repository = DocumentRepository(session)

    async def get_for_owner(
        self,
        owner_id: UUID,
        document_id: UUID,
    ) -> DocumentRecord | None:
        try:
            document = await self.repository.get_for_owner(owner_id, document_id)
            await self.session.rollback()
            return None if document is None else _document_record(document)
        except BaseException:
            await self.session.rollback()
            raise

    async def list_for_owner(self, owner_id: UUID) -> tuple[DocumentRecord, ...]:
        try:
            documents = await self.repository.list_for_owner(owner_id)
            records = tuple(_document_record(document) for document in documents)
            await self.session.rollback()
            return records
        except BaseException:
            await self.session.rollback()
            raise

    async def ingest_for_owner(
        self,
        owner_id: UUID,
        asset_id: UUID,
    ) -> DocumentRecord:
        if self.storage is None or self.admission is None:
            raise DocumentContentUnavailableError(
                "document content is unavailable"
            )
        document_id: UUID | None = None
        ingestion_token: UUID | None = None
        try:
            asset = await self.repository.get_active_asset(owner_id, asset_id)
            if asset is None:
                await self.session.rollback()
                raise DocumentNotFoundError("document asset is unavailable")
            if asset.media_type not in SUPPORTED_DOCUMENT_MEDIA_TYPES:
                await self.session.rollback()
                raise DocumentUnsupportedError("document media type is unsupported")

            document = await self.repository.get_for_owner_asset(owner_id, asset_id)
            if document is None:
                try:
                    document = await self.repository.create_pending(owner_id, asset_id)
                    await self.session.commit()
                except IntegrityError:
                    await self.session.rollback()
                    document = await self.repository.get_for_owner_asset(
                        owner_id,
                        asset_id,
                    )
                    if document is None:
                        raise
            if document.status in {DocumentStatus.READY, DocumentStatus.PROCESSING}:
                record = _document_record(document)
                await self.session.rollback()
                return record

            document_id = document.id
            ingestion_token = uuid4()
            claimed = await self.repository.claim(
                owner_id,
                document_id,
                ingestion_token,
            )
            if not claimed:
                await self.session.rollback()
                current = await self.repository.get_for_owner(owner_id, document_id)
                await self.session.rollback()
                if current is None:
                    raise DocumentNotFoundError("document is unavailable")
                return _document_record(current)
            snapshot = await self.repository.snapshot_for_processing(
                owner_id,
                document_id,
                ingestion_token,
            )
            if snapshot is None:
                await self.session.rollback()
                raise DocumentNotFoundError("document asset is unavailable")
            await self.session.commit()

            acquired = False
            active_key = (owner_id, document_id)
            active_task = asyncio.current_task()
            if self.active_tasks is not None and active_task is not None:
                self.active_tasks[active_key] = active_task
            try:
                async with asyncio.timeout(self.max_duration_seconds):
                    await self.admission.acquire()
                    acquired = True
                    data = await _cancellable_thread(
                        lambda cancel_event: _read_verified_document(
                            cancel_event,
                            self.storage,
                            snapshot,
                        )
                    )
                    try:
                        drafts = await _cancellable_thread(
                            lambda cancel_event: _run_parser_process(
                                cancel_event,
                                data,
                                snapshot.media_type,
                            )
                        )
                    finally:
                        data = b""
                    indexed = await _cancellable_thread(
                        lambda cancel_event: _embed_chunks(cancel_event, drafts)
                    )
            finally:
                if acquired:
                    self.admission.release()
                if (
                    self.active_tasks is not None
                    and self.active_tasks.get(active_key) is active_task
                ):
                    self.active_tasks.pop(active_key, None)

            chunks = tuple(
                DocumentChunk(
                    owner_id=owner_id,
                    document_id=document_id,
                    asset_id=asset_id,
                    ordinal=item.draft.ordinal,
                    content=item.draft.content,
                    embedding=item.embedding,
                    embedding_norm=item.embedding_norm,
                    provenance_kind=item.draft.provenance_kind,
                    page_number=item.draft.page_number,
                    row_start=item.draft.row_start,
                    row_end=item.draft.row_end,
                    section=item.draft.section,
                )
                for item in indexed
            )
            completed = await self.repository.complete(
                owner_id,
                document_id,
                ingestion_token,
                chunks,
                sum(len(item.draft.content) for item in indexed),
            )
            if not completed:
                await self.session.rollback()
                raise DocumentIngestionUnavailableError(
                    "document ingestion state changed"
                )
            await self.session.commit()
            ready = await self.repository.get_for_owner(owner_id, document_id)
            await self.session.rollback()
            if ready is None:
                raise DocumentIngestionUnavailableError(
                    "document ingestion result is unavailable"
                )
            return _document_record(ready)
        except asyncio.CancelledError:
            if document_id is not None and ingestion_token is not None:
                await self._finish_unsuccessfully(
                    owner_id,
                    document_id,
                    ingestion_token,
                    status=DocumentStatus.CANCELLED,
                    failure_code="cancelled",
                )
            raise
        except DocumentIngestionRejectedError as exc:
            if document_id is not None and ingestion_token is not None:
                await self._finish_unsuccessfully(
                    owner_id,
                    document_id,
                    ingestion_token,
                    status=DocumentStatus.FAILED,
                    failure_code=exc.failure_code,
                )
            raise
        except TimeoutError as exc:
            if document_id is not None and ingestion_token is not None:
                await self._finish_unsuccessfully(
                    owner_id,
                    document_id,
                    ingestion_token,
                    status=DocumentStatus.FAILED,
                    failure_code="timed_out",
                )
            raise DocumentIngestionUnavailableError(
                "local document ingestion timed out"
            ) from exc
        except DocumentContentUnavailableError:
            if document_id is not None and ingestion_token is not None:
                await self._finish_unsuccessfully(
                    owner_id,
                    document_id,
                    ingestion_token,
                    status=DocumentStatus.FAILED,
                    failure_code="content_unavailable",
                )
            raise
        except (DocumentNotFoundError, DocumentUnsupportedError):
            await self.session.rollback()
            raise
        except BaseException as exc:
            await self.session.rollback()
            if document_id is not None and ingestion_token is not None:
                await self._finish_unsuccessfully(
                    owner_id,
                    document_id,
                    ingestion_token,
                    status=DocumentStatus.FAILED,
                    failure_code="ingestion_unavailable",
                )
            if isinstance(exc, DocumentIngestionUnavailableError):
                raise
            raise DocumentIngestionUnavailableError(
                "local document ingestion is unavailable"
            ) from exc

    async def cancel_for_owner(
        self,
        owner_id: UUID,
        document_id: UUID,
    ) -> bool:
        try:
            document = await self.repository.get_for_owner(owner_id, document_id)
            if document is None:
                await self.session.rollback()
                return False
            if document.status is not DocumentStatus.PROCESSING:
                await self.session.rollback()
                return True
            cancelled = await self.repository.cancel_for_owner(owner_id, document_id)
            await self.session.commit()
            active_task = (
                self.active_tasks.get((owner_id, document_id))
                if self.active_tasks is not None
                else None
            )
            if (
                cancelled
                and active_task is not None
                and not active_task.done()
                and active_task.cancelling() == 0
            ):
                active_task.cancel()
            return cancelled
        except BaseException:
            await self.session.rollback()
            raise

    async def _finish_unsuccessfully(
        self,
        owner_id: UUID,
        document_id: UUID,
        ingestion_token: UUID,
        *,
        status: DocumentStatus,
        failure_code: str,
    ) -> None:
        try:
            await self.session.rollback()
            await self.repository.finish_unsuccessfully(
                owner_id,
                document_id,
                ingestion_token,
                status=status,
                failure_code=failure_code,
            )
            await self.session.commit()
        except BaseException:
            await self.session.rollback()

    async def search_for_owner(
        self,
        owner_id: UUID,
        query: str,
        *,
        limit: int = MAX_RETRIEVAL_RESULTS,
    ) -> tuple[RetrievedDocumentChunk, ...]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("document search query must not be blank")
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("document search limit must be an integer")
        if not 1 <= limit <= MAX_RETRIEVAL_RESULTS:
            raise ValueError("document search limit is outside its bound")

        try:
            query_embedding = await _cancellable_thread(
                lambda cancel_event: embed_text(query)
            )
        except EmbeddingError:
            return ()
        try:
            candidates = await self.repository.list_retrieval_candidates(owner_id)
            await self.session.rollback()
        except BaseException:
            await self.session.rollback()
            raise

        scored: list[tuple[float, RetrievalCandidate]] = []
        for candidate in candidates:
            try:
                score = cosine_similarity(
                    query_embedding.packed,
                    candidate.embedding,
                )
            except EmbeddingError:
                continue
            if score > 0:
                scored.append((score, candidate))
        scored.sort(key=lambda item: (-item[0], str(item[1].chunk_id)))

        selected: list[RetrievedDocumentChunk] = []
        per_asset: dict[UUID, int] = {}
        character_count = 0
        for score, candidate in scored:
            if per_asset.get(candidate.asset_id, 0) >= 2:
                continue
            if len(candidate.content) > MAX_RETRIEVAL_CONTEXT_CHARACTERS - character_count:
                continue
            selected.append(
                RetrievedDocumentChunk(
                    chunk_id=candidate.chunk_id,
                    asset_id=candidate.asset_id,
                    content=candidate.content,
                    score=score,
                    original_filename=candidate.original_filename,
                    provenance_kind=candidate.provenance_kind,
                    page_number=candidate.page_number,
                    row_start=candidate.row_start,
                    row_end=candidate.row_end,
                    section=candidate.section,
                )
            )
            per_asset[candidate.asset_id] = per_asset.get(candidate.asset_id, 0) + 1
            character_count += len(candidate.content)
            if len(selected) >= limit:
                break
        return tuple(selected)
