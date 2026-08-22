from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.documents.embedding import embed_text
from app.models.memory import Memory, MemoryCategory
from app.repositories.memory import MemoryCandidate
from app.services.memory import MemoryContentInvalidError, MemoryService


def _memory(owner_id, *, deleted=False):
    now = datetime(2026, 8, 22, tzinfo=timezone.utc)
    return Memory(
        id=uuid4(),
        owner_id=owner_id,
        category=MemoryCategory.PREFERENCE,
        content=None if deleted else "Use concise answers.",
        embedding=None if deleted else embed_text("Use concise answers.").packed,
        embedding_norm=None if deleted else 1.0,
        provenance_kind="explicit_user_entry",
        created_at=now,
        updated_at=now,
        deleted_at=now if deleted else None,
    )


@pytest.mark.asyncio
async def test_create_is_explicit_bounded_and_embeds_before_database_write():
    owner_id = uuid4()
    memory = _memory(owner_id)
    session = AsyncMock(spec=AsyncSession)
    service = MemoryService(session)
    events = []

    async def create(*args):
        events.append("database")
        return memory

    service.repository = Mock(create=AsyncMock(side_effect=create))

    result = await service.create_for_owner(
        owner_id,
        MemoryCategory.PREFERENCE,
        "Use concise answers.",
    )

    assert result.content == "Use concise answers."
    assert result.provenance_kind == "explicit_user_entry"
    assert service.repository.create.await_args.args[0:3] == (
        owner_id,
        MemoryCategory.PREFERENCE,
        "Use concise answers.",
    )
    assert len(service.repository.create.await_args.args[3]) == 1024
    assert events == ["database"]
    session.commit.assert_awaited_once()


@pytest.mark.parametrize("content", ["", "   ", "x" * 2001, "!@#$"])
@pytest.mark.asyncio
async def test_invalid_or_non_embeddable_memory_never_reaches_database(content):
    session = AsyncMock(spec=AsyncSession)
    service = MemoryService(session)
    service.repository = Mock(create=AsyncMock())

    with pytest.raises(MemoryContentInvalidError):
        await service.create_for_owner(
            uuid4(),
            MemoryCategory.FACT,
            content,
        )

    service.repository.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_forget_returns_content_free_tombstone_and_is_idempotent():
    owner_id = uuid4()
    active = _memory(owner_id)
    deleted = _memory(owner_id, deleted=True)
    deleted.id = active.id
    session = AsyncMock(spec=AsyncSession)
    service = MemoryService(session)
    repository = Mock()
    repository.get_for_owner = AsyncMock(side_effect=[active, deleted])
    repository.forget_for_owner = AsyncMock(return_value=deleted)
    service.repository = repository

    first = await service.forget_for_owner(owner_id, active.id)
    second = await service.forget_for_owner(owner_id, active.id)

    assert first is not None and first.content is None and first.deleted_at is not None
    assert second is not None and second.content is None
    repository.forget_for_owner.assert_awaited_once_with(owner_id, active.id)
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_disabled_memory_skips_candidate_materialization():
    session = AsyncMock(spec=AsyncSession)
    service = MemoryService(session)
    repository = Mock()
    repository.setting_for_owner = AsyncMock(
        return_value=SimpleNamespace(enabled=False)
    )
    repository.list_retrieval_candidates = AsyncMock()
    service.repository = repository

    assert await service.retrieve_for_owner(uuid4(), "current question") == ()
    repository.list_retrieval_candidates.assert_not_awaited()
    session.rollback.assert_awaited_once()


def test_retrieval_includes_global_instructions_and_relevant_owned_context():
    now = datetime(2026, 8, 22, tzinfo=timezone.utc)
    instruction = MemoryCandidate(
        uuid4(),
        MemoryCategory.INSTRUCTION,
        "Always answer concisely.",
        embed_text("Always answer concisely.").packed,
        now,
    )
    project = MemoryCandidate(
        uuid4(),
        MemoryCategory.PROJECT_CONTEXT,
        "The Apollo project deadline is Friday.",
        embed_text("The Apollo project deadline is Friday.").packed,
        now,
    )
    unrelated = MemoryCandidate(
        uuid4(),
        MemoryCategory.FACT,
        "The garden has roses.",
        embed_text("The garden has roses.").packed,
        now,
    )

    selected = MemoryService._select(
        embed_text("When is the Apollo project deadline?").packed,
        (instruction, project, unrelated),
        8,
    )

    assert {item.id for item in selected} == {instruction.id, project.id}
    assert sum(len(item.content) for item in selected) <= 4_000


@pytest.mark.asyncio
async def test_concurrent_forget_returns_the_winning_content_free_tombstone():
    owner_id = uuid4()
    active = _memory(owner_id)
    deleted = _memory(owner_id, deleted=True)
    deleted.id = active.id
    session = AsyncMock(spec=AsyncSession)
    service = MemoryService(session)
    repository = Mock()
    repository.get_for_owner = AsyncMock(side_effect=[active, deleted])
    repository.forget_for_owner = AsyncMock(return_value=None)
    service.repository = repository

    result = await service.forget_for_owner(owner_id, active.id)

    assert result is not None
    assert result.content is None
    assert result.deleted_at is not None
    assert repository.get_for_owner.await_count == 2
    session.commit.assert_not_awaited()
