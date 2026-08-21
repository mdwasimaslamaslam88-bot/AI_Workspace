from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import Update

from app.models import Asset
from app.repositories.asset import AssetRepository


def _session_with_scalar(value):
    session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.scalar_one_or_none.return_value = value
    session.execute.return_value = result
    return session


def _compiled(statement):
    compiled = statement.compile(dialect=postgresql.dialect())
    return compiled, " ".join(str(compiled).lower().split())


@pytest.mark.asyncio
async def test_active_lookup_requires_both_owner_and_non_deleted_state():
    owner_id = uuid4()
    asset_id = uuid4()
    session = _session_with_scalar(None)

    found = await AssetRepository(session).get_active_for_owner(owner_id, asset_id)

    assert found is None
    compiled, sql = _compiled(session.execute.await_args.args[0])
    assert "assets.id =" in sql
    assert "assets.owner_id =" in sql
    assert "assets.deleted_at is null" in sql
    assert owner_id in compiled.params.values()
    assert asset_id in compiled.params.values()


@pytest.mark.asyncio
async def test_idempotency_lookup_is_owner_scoped():
    owner_id = uuid4()
    key = uuid4()
    session = _session_with_scalar(None)

    await AssetRepository(session).get_by_idempotency_key_for_owner(owner_id, key)

    compiled, sql = _compiled(session.execute.await_args.args[0])
    assert "assets.owner_id =" in sql
    assert "assets.upload_idempotency_key =" in sql
    assert owner_id in compiled.params.values()
    assert key in compiled.params.values()


@pytest.mark.asyncio
async def test_soft_delete_is_owner_scoped_and_idempotent():
    asset = Asset(
        id=uuid4(),
        owner_id=uuid4(),
        original_filename=None,
        media_type="application/octet-stream",
        byte_size=1,
        content_sha256="a" * 64,
        storage_key="objects/aa/aa/" + "a" * 32,
        upload_idempotency_key=uuid4(),
        created_at=datetime.now(timezone.utc),
    )
    session = _session_with_scalar(asset)

    returned = await AssetRepository(session).soft_delete_for_owner(
        asset.owner_id,
        asset.id,
    )

    assert returned is asset
    statement = session.execute.await_args.args[0]
    assert isinstance(statement, Update)
    compiled, sql = _compiled(statement)
    assert "where assets.id =" in sql
    assert "and assets.owner_id =" in sql
    assert "deleted_at=coalesce(assets.deleted_at, now())" in sql
    assert "returning assets.id" in sql
    assert asset.owner_id in compiled.params.values()
