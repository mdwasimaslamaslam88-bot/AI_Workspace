from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.document import DocumentRepository


@pytest.mark.asyncio
async def test_retrieval_query_scopes_chunk_document_and_asset_to_owner():
    session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.all.return_value = []
    session.execute.return_value = result
    owner_id = uuid4()

    assert (
        await DocumentRepository(session).list_retrieval_candidates(owner_id)
        == ()
    )

    statement = session.execute.await_args.args[0]
    sql = str(statement)
    assert "document_chunks.owner_id" in sql
    assert "documents.owner_id" in sql
    assert "assets.owner_id" in sql
    assert "assets.deleted_at IS NULL" in sql
    assert "documents.status" in sql


@pytest.mark.asyncio
async def test_completion_requires_same_owner_active_asset():
    session = AsyncMock(spec=AsyncSession)
    result = Mock(rowcount=0)
    session.execute.return_value = result
    owner_id = uuid4()

    completed = await DocumentRepository(session).complete(
        owner_id,
        uuid4(),
        uuid4(),
        (),
        0,
    )

    assert not completed
    statement = session.execute.await_args.args[0]
    sql = str(statement)
    assert "documents.owner_id" in sql
    assert "assets.owner_id" in sql
    assert "assets.deleted_at IS NULL" in sql
    session.add_all.assert_not_called()
