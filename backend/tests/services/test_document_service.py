import asyncio
import threading
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.document as document_module
from app.documents.embedding import embed_text, pack_embedding
from app.documents.parsers import DocumentChunkDraft
from app.models.document import DocumentStatus
from app.repositories.document import DocumentAssetSnapshot, RetrievalCandidate
from app.services.document import (
    DocumentContentUnavailableError,
    DocumentRetrievalUnavailableError,
    DocumentService,
    IndexedChunk,
    _WorkCancelled,
)


def _document(asset, *, status: DocumentStatus):
    now = datetime(2026, 8, 22, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=uuid4(),
        asset_id=asset.id,
        asset=asset,
        status=status,
        chunk_count=0,
        character_count=0,
        failure_code=None,
        created_at=now,
        updated_at=now,
        completed_at=now if status is DocumentStatus.READY else None,
    )


def _service(repository, *, active_tasks=None):
    session = AsyncMock(spec=AsyncSession)
    service = DocumentService(
        session,
        Mock(),
        asyncio.Semaphore(1),
        active_tasks=active_tasks,
    )
    service.repository = repository
    return service, session


@pytest.mark.asyncio
async def test_ingestion_commits_claim_before_parser_and_persists_after_embedding(
    monkeypatch,
):
    owner_id = uuid4()
    asset_id = uuid4()
    asset = SimpleNamespace(
        id=asset_id,
        media_type="text/plain",
        deleted_at=None,
        original_filename="notes.txt",
    )
    processing = _document(asset, status=DocumentStatus.FAILED)
    ready = _document(asset, status=DocumentStatus.READY)
    ready.id = processing.id
    snapshot = DocumentAssetSnapshot(
        document_id=processing.id,
        asset_id=asset_id,
        media_type="text/plain",
        byte_size=5,
        content_sha256="a" * 64,
        storage_key="opaque-key",
        original_filename="notes.txt",
    )
    repository = Mock()
    repository.get_active_asset = AsyncMock(return_value=asset)
    repository.get_for_owner_asset = AsyncMock(return_value=processing)
    repository.claim = AsyncMock(return_value=True)
    repository.snapshot_for_processing = AsyncMock(return_value=snapshot)
    repository.complete = AsyncMock(return_value=True)
    repository.get_for_owner = AsyncMock(return_value=ready)
    repository.finish_unsuccessfully = AsyncMock(return_value=True)
    events: list[str] = []
    service, session = _service(repository)

    async def commit():
        events.append("commit")

    async def complete(*args, **kwargs):
        events.append("complete")
        return True

    draft = DocumentChunkDraft(1, "local text", "text", None, None, None, None)
    embedding = embed_text(draft.content)
    indexed = (IndexedChunk(draft, embedding.packed, embedding.norm),)
    session.commit.side_effect = commit
    repository.complete.side_effect = complete

    def read(*_args):
        events.append("read")
        return b"local"

    def parse(*_args):
        events.append("parse")
        return (draft,)

    def embed(*_args):
        events.append("embed")
        return indexed

    monkeypatch.setattr(document_module, "_read_verified_document", read)
    monkeypatch.setattr(document_module, "_run_parser_process", parse)
    monkeypatch.setattr(document_module, "_embed_chunks", embed)

    result = await service.ingest_for_owner(owner_id, asset_id)

    assert result.status is DocumentStatus.READY
    assert events == ["commit", "read", "parse", "embed", "complete", "commit"]
    chunks = repository.complete.await_args.args[3]
    assert len(chunks) == 1
    assert chunks[0].owner_id == owner_id
    assert chunks[0].asset_id == asset_id
    session.rollback.assert_awaited()


@pytest.mark.asyncio
async def test_ingestion_persists_configured_runtime_identity_and_dimensions(
    monkeypatch,
):
    owner_id = uuid4()
    asset_id = uuid4()
    asset = SimpleNamespace(
        id=asset_id,
        media_type="text/plain",
        deleted_at=None,
        original_filename="notes.txt",
    )
    processing = _document(asset, status=DocumentStatus.FAILED)
    ready = _document(asset, status=DocumentStatus.READY)
    ready.id = processing.id
    snapshot = DocumentAssetSnapshot(
        document_id=processing.id,
        asset_id=asset_id,
        media_type="text/plain",
        byte_size=5,
        content_sha256="a" * 64,
        storage_key="opaque-key",
        original_filename="notes.txt",
    )
    repository = Mock(
        get_active_asset=AsyncMock(return_value=asset),
        get_for_owner_asset=AsyncMock(return_value=processing),
        claim=AsyncMock(return_value=True),
        snapshot_for_processing=AsyncMock(return_value=snapshot),
        complete=AsyncMock(return_value=True),
        get_for_owner=AsyncMock(return_value=ready),
        finish_unsuccessfully=AsyncMock(return_value=True),
    )
    draft = DocumentChunkDraft(1, "local text", "text", None, None, None, None)
    runtime_embedding = pack_embedding(
        (1.0, 2.0, 3.0),
        "ollama:nomic-embed-text:latest",
    )
    runtime = Mock(
        model_id="ollama:nomic-embed-text:latest",
        embed_texts=AsyncMock(return_value=(runtime_embedding,)),
    )
    service, _session = _service(repository)
    service.embedding_runtime = runtime
    monkeypatch.setattr(document_module, "_read_verified_document", lambda *_: b"local")
    monkeypatch.setattr(document_module, "_run_parser_process", lambda *_: (draft,))

    result = await service.ingest_for_owner(owner_id, asset_id)

    assert result.status is DocumentStatus.READY
    runtime.embed_texts.assert_awaited_once_with(("local text",))
    chunk = repository.complete.await_args.args[3][0]
    assert chunk.embedding_model == "ollama:nomic-embed-text:latest"
    assert chunk.embedding_dimensions == 3
    assert len(chunk.embedding) == 12


@pytest.mark.asyncio
async def test_search_scores_only_owner_candidates_with_matching_runtime_model():
    owner_id = uuid4()
    asset_id = uuid4()
    model_id = "ollama:nomic-embed-text:latest"
    stored = pack_embedding((1.0, 0.0, 0.0), model_id)
    query = pack_embedding((1.0, 0.0, 0.0), model_id)
    candidate = RetrievalCandidate(
        chunk_id=uuid4(),
        asset_id=asset_id,
        content="owned source",
        embedding=stored.packed,
        embedding_model=model_id,
        embedding_dimensions=stored.dimensions,
        provenance_kind="text",
        page_number=None,
        row_start=None,
        row_end=None,
        section=None,
        original_filename="owned.txt",
    )
    repository = Mock(
        list_retrieval_candidates=AsyncMock(return_value=(candidate,))
    )
    runtime = Mock(
        model_id=model_id,
        embed_texts=AsyncMock(return_value=(query,)),
    )
    service, session = _service(repository)
    service.embedding_runtime = runtime

    results = await service.search_for_owner(owner_id, "owned", limit=1)

    repository.list_retrieval_candidates.assert_awaited_once_with(owner_id)
    session.rollback.assert_awaited_once()
    runtime.embed_texts.assert_awaited_once_with(("owned",))
    assert len(results) == 1
    assert results[0].asset_id == asset_id
    assert results[0].score == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_search_converts_embedding_failure_to_safe_retrieval_error():
    owner_id = uuid4()
    model_id = "ollama:nomic-embed-text:latest"
    stored = pack_embedding((1.0, 0.0), model_id)
    candidate = RetrievalCandidate(
        chunk_id=uuid4(),
        asset_id=uuid4(),
        content="owned source",
        embedding=stored.packed,
        embedding_model=model_id,
        embedding_dimensions=stored.dimensions,
        provenance_kind="text",
        page_number=None,
        row_start=None,
        row_end=None,
        section=None,
        original_filename="owned.txt",
    )
    repository = Mock(
        list_retrieval_candidates=AsyncMock(return_value=(candidate,))
    )
    runtime = Mock(
        model_id=model_id,
        embed_texts=AsyncMock(side_effect=RuntimeError("private runtime detail")),
    )
    service, _session = _service(repository)
    service.embedding_runtime = runtime

    with pytest.raises(DocumentRetrievalUnavailableError) as captured:
        await service.search_for_owner(owner_id, "owned")

    assert str(captured.value) == "local document retrieval is unavailable"
    assert "private runtime detail" not in str(captured.value)


@pytest.mark.asyncio
async def test_search_uses_compatible_legacy_fallback_when_runtime_fails():
    owner_id = uuid4()
    remote_model = "ollama:nomic-embed-text:latest"
    remote = pack_embedding((1.0, 0.0), remote_model)
    legacy = embed_text("owned")

    def candidate(model_id, embedding, dimensions, content):
        return RetrievalCandidate(
            chunk_id=uuid4(),
            asset_id=uuid4(),
            content=content,
            embedding=embedding,
            embedding_model=model_id,
            embedding_dimensions=dimensions,
            provenance_kind="text",
            page_number=None,
            row_start=None,
            row_end=None,
            section=None,
            original_filename="owned.txt",
        )

    repository = Mock(
        list_retrieval_candidates=AsyncMock(
            return_value=(
                candidate(remote_model, remote.packed, remote.dimensions, "remote"),
                candidate(
                    legacy.model_id,
                    legacy.packed,
                    legacy.dimensions,
                    "legacy",
                ),
            )
        )
    )
    runtime = Mock(
        model_id=remote_model,
        embed_texts=AsyncMock(side_effect=RuntimeError("runtime offline")),
    )
    service, _session = _service(repository)
    service.embedding_runtime = runtime

    results = await service.search_for_owner(owner_id, "owned")

    assert [item.content for item in results] == ["legacy"]


@pytest.mark.asyncio
async def test_unavailable_content_transitions_claimed_document_out_of_processing(
    monkeypatch,
):
    owner_id = uuid4()
    asset_id = uuid4()
    asset = SimpleNamespace(
        id=asset_id,
        media_type="text/plain",
        deleted_at=None,
        original_filename="notes.txt",
    )
    document = _document(asset, status=DocumentStatus.FAILED)
    snapshot = DocumentAssetSnapshot(
        document_id=document.id,
        asset_id=asset_id,
        media_type="text/plain",
        byte_size=5,
        content_sha256="a" * 64,
        storage_key="opaque-key",
        original_filename="notes.txt",
    )
    repository = Mock()
    repository.get_active_asset = AsyncMock(return_value=asset)
    repository.get_for_owner_asset = AsyncMock(return_value=document)
    repository.claim = AsyncMock(return_value=True)
    repository.snapshot_for_processing = AsyncMock(return_value=snapshot)
    repository.finish_unsuccessfully = AsyncMock(return_value=True)
    service, _session = _service(repository)

    def fail_read(*_args):
        raise DocumentContentUnavailableError()

    monkeypatch.setattr(document_module, "_read_verified_document", fail_read)

    with pytest.raises(DocumentContentUnavailableError):
        await service.ingest_for_owner(owner_id, asset_id)

    repository.finish_unsuccessfully.assert_awaited_once()
    assert repository.finish_unsuccessfully.await_args.kwargs == {
        "status": DocumentStatus.FAILED,
        "failure_code": "content_unavailable",
    }


@pytest.mark.asyncio
async def test_cancel_endpoint_signals_active_work_and_cleans_registry(monkeypatch):
    owner_id = uuid4()
    asset_id = uuid4()
    asset = SimpleNamespace(
        id=asset_id,
        media_type="text/plain",
        deleted_at=None,
        original_filename="notes.txt",
    )
    document = _document(asset, status=DocumentStatus.FAILED)
    processing = _document(asset, status=DocumentStatus.PROCESSING)
    processing.id = document.id
    snapshot = DocumentAssetSnapshot(
        document_id=document.id,
        asset_id=asset_id,
        media_type="text/plain",
        byte_size=5,
        content_sha256="a" * 64,
        storage_key="opaque-key",
        original_filename="notes.txt",
    )
    ingest_repository = Mock()
    ingest_repository.get_active_asset = AsyncMock(return_value=asset)
    ingest_repository.get_for_owner_asset = AsyncMock(return_value=document)
    ingest_repository.claim = AsyncMock(return_value=True)
    ingest_repository.snapshot_for_processing = AsyncMock(return_value=snapshot)
    ingest_repository.finish_unsuccessfully = AsyncMock(return_value=True)
    active_tasks = {}
    ingest_service, _ingest_session = _service(
        ingest_repository,
        active_tasks=active_tasks,
    )
    worker_stopped = threading.Event()

    def block_until_cancelled(cancel_event, *_args):
        while not cancel_event.wait(0.01):
            pass
        worker_stopped.set()
        raise _WorkCancelled()

    monkeypatch.setattr(
        document_module,
        "_read_verified_document",
        block_until_cancelled,
    )
    ingestion = asyncio.create_task(
        ingest_service.ingest_for_owner(owner_id, asset_id)
    )
    for _ in range(100):
        if active_tasks:
            break
        await asyncio.sleep(0.01)
    assert active_tasks == {(owner_id, document.id): ingestion}

    cancel_repository = Mock()
    cancel_repository.get_for_owner = AsyncMock(return_value=processing)
    cancel_repository.cancel_for_owner = AsyncMock(return_value=True)
    cancel_service, _cancel_session = _service(
        cancel_repository,
        active_tasks=active_tasks,
    )

    assert await cancel_service.cancel_for_owner(owner_id, document.id)
    with pytest.raises(asyncio.CancelledError):
        await ingestion

    assert worker_stopped.is_set()
    assert active_tasks == {}
    ingest_repository.finish_unsuccessfully.assert_awaited_once()
    assert ingest_repository.finish_unsuccessfully.await_args.kwargs == {
        "status": DocumentStatus.CANCELLED,
        "failure_code": "cancelled",
    }


@pytest.mark.asyncio
async def test_concurrent_contender_observes_processing_without_duplicate_work():
    owner_id = uuid4()
    asset_id = uuid4()
    asset = SimpleNamespace(
        id=asset_id,
        media_type="text/plain",
        deleted_at=None,
        original_filename="notes.txt",
    )
    processing = _document(asset, status=DocumentStatus.PROCESSING)
    repository = Mock()
    repository.get_active_asset = AsyncMock(return_value=asset)
    repository.get_for_owner_asset = AsyncMock(return_value=processing)
    repository.claim = AsyncMock()
    service, _session = _service(repository)

    result = await service.ingest_for_owner(owner_id, asset_id)

    assert result.status is DocumentStatus.PROCESSING
    repository.claim.assert_not_awaited()
