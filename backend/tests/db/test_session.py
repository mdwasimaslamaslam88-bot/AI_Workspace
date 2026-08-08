import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from unittest.mock import Mock, AsyncMock
from app.db.dependencies import get_db_session
from app.db.session import create_session_factory, session_scope

def test_session_factory_is_optional_without_engine():
    assert create_session_factory(None) is None

def test_session_factory_uses_async_sessions():
    factory = create_session_factory(Mock(spec=AsyncEngine))
    assert isinstance(factory(), AsyncSession)

@pytest.mark.asyncio
async def test_session_scope_only_manages_session_lifetime():
    session = AsyncMock(); session.__aenter__.return_value = session; factory = Mock(return_value=session)
    with pytest.raises(ValueError):
        async with session_scope(factory):
            raise ValueError("failure")
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()
    session.__aexit__.assert_awaited_once()

@pytest.mark.asyncio
async def test_get_db_session_does_not_commit_successful_request():
    session = AsyncMock(); session.__aenter__.return_value = session; factory = Mock(return_value=session)
    request = Mock(); request.app.state.db_session_factory = factory

    dependency = get_db_session(request)
    assert await anext(dependency) is session
    with pytest.raises(StopAsyncIteration):
        await anext(dependency)

    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()
    session.__aexit__.assert_awaited_once()

@pytest.mark.asyncio
async def test_get_db_session_rolls_back_failed_request():
    session = AsyncMock(); session.__aenter__.return_value = session; factory = Mock(return_value=session)
    request = Mock(); request.app.state.db_session_factory = factory

    dependency = get_db_session(request)
    assert await anext(dependency) is session
    with pytest.raises(ValueError, match="failure"):
        await dependency.athrow(ValueError("failure"))

    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.__aexit__.assert_awaited_once()
