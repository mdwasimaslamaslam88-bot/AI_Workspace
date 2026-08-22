from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.memory import MemoryRepository


@pytest.mark.asyncio
async def test_retrieval_query_is_owner_scoped_and_excludes_forgotten_content():
    session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.all.return_value = []
    session.execute.return_value = result
    owner_id = uuid4()

    assert await MemoryRepository(session).list_retrieval_candidates(owner_id) == ()

    sql = str(session.execute.await_args.args[0])
    assert "memories.owner_id" in sql
    assert "memories.deleted_at IS NULL" in sql
    assert "memories.content IS NOT NULL" in sql
    assert "memories.embedding IS NOT NULL" in sql
