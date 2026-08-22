from datetime import datetime, timezone
import json
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tool import ToolExecution, ToolExecutionStatus
from app.services.tool import (
    TOOL_REGISTRY,
    ToolInputInvalidError,
    ToolNotFoundError,
    ToolService,
    _evaluate_arithmetic,
    reconcile_tool_executions,
)


def _execution(owner_id, *, status=ToolExecutionStatus.RUNNING, result=None):
    now = datetime(2026, 8, 22, tzinfo=timezone.utc)
    return ToolExecution(
        id=uuid4(),
        owner_id=owner_id,
        tool_name="calculator",
        permission="utility",
        status=status,
        initiator="explicit_user",
        arguments_json='{"expression":"2+3*4"}',
        result_json=json.dumps(result) if result is not None else None,
        error_code=None,
        started_at=now,
        completed_at=now if status is not ToolExecutionStatus.RUNNING else None,
        duration_ms=0 if status is not ToolExecutionStatus.RUNNING else None,
    )


def test_registry_is_fixed_and_contains_no_dangerous_capabilities():
    assert set(TOOL_REGISTRY) == {
        "calculator",
        "local_time",
        "document_search",
        "conversation_search",
        "memory_search",
    }
    serialized = json.dumps(
        [definition.public_schema() for definition in TOOL_REGISTRY.values()]
    ).lower()
    assert "shell" not in serialized
    assert "filesystem" not in serialized
    assert "network" not in serialized
    assert all(
        definition.max_output_characters <= 12_000
        and definition.timeout_seconds <= 5
        for definition in TOOL_REGISTRY.values()
    )


@pytest.mark.parametrize(
    ("expression", "expected"),
    [("2 + 3 * 4", 14), ("(10 - 4) / 3", 2.0), ("-5 % 3", 1)],
)
def test_calculator_supports_only_bounded_arithmetic(expression, expected):
    assert _evaluate_arithmetic(expression) == expected


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('id')",
        "open('/etc/passwd')",
        "x + 1",
        "2 ** 1000",
        "1 / 0",
        "[1, 2, 3]",
    ],
)
def test_calculator_rejects_code_names_functions_and_unbounded_work(expression):
    with pytest.raises(ToolInputInvalidError):
        _evaluate_arithmetic(expression)


@pytest.mark.asyncio
async def test_calculator_execution_is_a_durable_bounded_owner_audit():
    owner_id = uuid4()
    running = _execution(owner_id)
    completed = _execution(
        owner_id,
        status=ToolExecutionStatus.COMPLETED,
        result={"value": 14},
    )
    completed.id = running.id
    session = AsyncMock(spec=AsyncSession)
    repository = Mock()
    repository.create_running = AsyncMock(return_value=running)
    repository.finish = AsyncMock(return_value=completed)
    repository.conversation_exists_for_owner = AsyncMock(return_value=True)
    service = ToolService(session)
    service.repository = repository

    result = await service.execute_for_owner(
        owner_id, "calculator", {"expression": "2+3*4"}
    )

    assert result.status is ToolExecutionStatus.COMPLETED
    assert result.result == {"value": 14}
    repository.create_running.assert_awaited_once_with(
        owner_id,
        None,
        "calculator",
        "utility",
        '{"expression":"2+3*4"}',
        initiator="explicit_user",
    )
    assert repository.finish.await_args.args[:3] == (
        owner_id,
        running.id,
        ToolExecutionStatus.COMPLETED,
    )
    assert session.commit.await_count == 2


@pytest.mark.asyncio
async def test_unknown_or_invalid_tool_never_creates_an_audit_record():
    session = AsyncMock(spec=AsyncSession)
    service = ToolService(session)
    service.repository = Mock(create_running=AsyncMock())

    with pytest.raises(ToolNotFoundError):
        await service.execute_for_owner(uuid4(), "shell", {"command": "id"})
    with pytest.raises(ToolInputInvalidError):
        await service.execute_for_owner(
            uuid4(), "calculator", {"expression": "2+2", "extra": True}
        )

    service.repository.create_running.assert_not_awaited()


@pytest.mark.asyncio
async def test_foreign_conversation_context_is_rejected_before_audit_creation():
    session = AsyncMock(spec=AsyncSession)
    service = ToolService(session)
    repository = Mock()
    repository.conversation_exists_for_owner = AsyncMock(return_value=False)
    repository.create_running = AsyncMock()
    service.repository = repository

    from app.services.tool import ToolConversationNotFoundError

    with pytest.raises(ToolConversationNotFoundError):
        await service.execute_for_owner(
            uuid4(),
            "calculator",
            {"expression": "1+1"},
            conversation_id=uuid4(),
        )

    repository.create_running.assert_not_awaited()
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_startup_reconciliation_closes_interrupted_running_calls(monkeypatch):
    session = AsyncMock(spec=AsyncSession)
    context = AsyncMock()
    context.__aenter__.return_value = session
    factory = Mock(return_value=context)
    repository = Mock(reconcile_interrupted=AsyncMock(return_value=2))
    monkeypatch.setattr(
        "app.services.tool.ToolRepository",
        Mock(return_value=repository),
    )

    assert await reconcile_tool_executions(factory) == 2

    repository.reconcile_interrupted.assert_awaited_once_with()
    session.commit.assert_awaited_once_with()
