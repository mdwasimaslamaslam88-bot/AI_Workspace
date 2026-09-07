from datetime import datetime, timezone
import json
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.dex import DexGatewayError
from app.models.tool import ToolExecution, ToolExecutionStatus
from app.services.tool import (
    TOOL_REGISTRY,
    ToolInputInvalidError,
    ToolNotFoundError,
    ToolService,
    _evaluate_arithmetic,
    reconcile_tool_executions,
)


def _execution(
    owner_id,
    *,
    status=ToolExecutionStatus.RUNNING,
    result=None,
    tool_name="calculator",
    permission="utility",
    initiator="explicit_user",
    arguments_json='{"expression":"2+3*4"}',
):
    now = datetime(2026, 8, 22, tzinfo=timezone.utc)
    return ToolExecution(
        id=uuid4(),
        owner_id=owner_id,
        tool_name=tool_name,
        permission=permission,
        status=status,
        initiator=initiator,
        arguments_json=arguments_json,
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
        "filesystem.write",
        "filesystem.read",
        "filesystem.exists",
        "filesystem.list",
        "filesystem.stat",
        "dex.delegate",
    }
    serialized = json.dumps(
        [definition.public_schema() for definition in TOOL_REGISTRY.values()]
    ).lower()
    assert "shell" not in serialized
    assert "network" not in serialized
    assert all(definition.max_output_characters <= 16_384 for definition in TOOL_REGISTRY.values())
    assert all(
        definition.timeout_seconds <= (300 if definition.name == "dex.delegate" else 5)
        for definition in TOOL_REGISTRY.values()
    )
    assert all(
        "workflow" not in definition.allowed_initiators
        for definition in TOOL_REGISTRY.values()
        if definition.name.startswith("filesystem.")
    )
    assert ToolService.definitions(initiator="workflow") == tuple(
        definition
        for definition in TOOL_REGISTRY.values()
        if "workflow" in definition.allowed_initiators
        and not definition.name.startswith("filesystem.")
    )
    assert not any(
        definition.name.startswith("filesystem.")
        for definition in ToolService.definitions(filesystem_available=False)
    )
    assert "dex.delegate" not in {
        definition.name for definition in ToolService.definitions(dex_available=False)
    }
    assert "dex.delegate" not in {
        definition.name for definition in ToolService.definitions(dex_available=True)
    }
    assert "dex.delegate" in {
        definition.name
        for definition in ToolService.definitions(
            initiator="chat_model",
            dex_available=True,
        )
    }
    assert {
        definition.name
        for definition in ToolService.definitions(
            filesystem_available=True,
            dex_available=True,
        )
    } == {
        "calculator",
        "local_time",
        "document_search",
        "conversation_search",
        "memory_search",
    }


@pytest.mark.asyncio
async def test_privileged_orchestrator_tools_reject_direct_api_initiator():
    service = ToolService(AsyncMock(spec=AsyncSession))

    for tool_name, arguments in (
        ("filesystem.write", {"path": "result.txt", "content": "test"}),
        ("dex.delegate", {"request": "inspect repository"}),
    ):
        with pytest.raises(ToolNotFoundError):
            await service.execute_for_owner(uuid4(), tool_name, arguments)


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
async def test_database_backed_tool_retains_audit_identity_after_session_reset():
    owner_id = uuid4()
    running = _execution(owner_id)
    execution_id = running.id
    completed = _execution(
        owner_id,
        status=ToolExecutionStatus.COMPLETED,
        result={"items": []},
    )
    completed.id = execution_id
    session = AsyncMock(spec=AsyncSession)
    repository = Mock()
    repository.create_running = AsyncMock(return_value=running)
    repository.finish = AsyncMock(return_value=completed)
    repository.conversation_exists_for_owner = AsyncMock(return_value=True)
    service = ToolService(session)
    service.repository = repository

    async def invoke_and_expire(*_args):
        # Owner-scoped database tools roll back the shared session. Model that
        # ORM expiration by making the original instance identity unavailable.
        running.id = None
        return {"items": []}

    service._invoke = AsyncMock(side_effect=invoke_and_expire)

    result = await service.execute_for_owner(
        owner_id, "memory_search", {"query": "private fact"}
    )

    assert result.status is ToolExecutionStatus.COMPLETED
    assert repository.finish.await_args.args[:2] == (owner_id, execution_id)


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
async def test_permission_denial_is_a_durable_failed_audit_before_invocation():
    owner_id = uuid4()
    running = _execution(owner_id)
    failed = _execution(owner_id, status=ToolExecutionStatus.FAILED)
    failed.id = running.id
    failed.error_code = "tool_permission_denied"
    session = AsyncMock(spec=AsyncSession)
    repository = Mock(
        create_running=AsyncMock(return_value=running),
        finish=AsyncMock(return_value=failed),
        conversation_exists_for_owner=AsyncMock(return_value=True),
    )
    service = ToolService(session)
    service.repository = repository
    service._invoke = AsyncMock()

    result = await service.execute_for_owner(
        owner_id,
        "calculator",
        {"expression": "2+2"},
        initiator="chat_model",
        allowed_permissions=frozenset({"workspace_read"}),
    )

    assert result.status is ToolExecutionStatus.FAILED
    assert result.error_code == "tool_permission_denied"
    service._invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_dex_delegation_reuses_owner_audit_and_redacts_task_and_result():
    owner_id = uuid4()
    running = _execution(
        owner_id,
        tool_name="dex.delegate",
        permission="agent_delegation",
        arguments_json="{}",
    )
    completed = _execution(
        owner_id,
        status=ToolExecutionStatus.COMPLETED,
        result={"status": "VERIFIED"},
        tool_name="dex.delegate",
        permission="agent_delegation",
        arguments_json="{}",
    )
    completed.id = running.id
    session = AsyncMock(spec=AsyncSession)
    repository = Mock(
        create_running=AsyncMock(return_value=running),
        finish=AsyncMock(return_value=completed),
        conversation_exists_for_owner=AsyncMock(return_value=True),
    )
    gateway = Mock(available=True)
    gateway.delegate = AsyncMock(
        return_value={
            "task_id": str(running.id),
            "correlation_id": str(uuid4()),
            "owner_id": str(owner_id),
            "status": "VERIFIED",
            "result": {"summary": "private task result"},
            "verification": {"status": "VERIFIED"},
            "timeline": [{"status": "VERIFIED"}],
        }
    )
    service = ToolService(session, dex_gateway=gateway)
    service.repository = repository

    result = await service.execute_for_owner(
        owner_id,
        "dex.delegate",
        {
            "request": "inspect private repository state",
            "capability": "verification",
            "scope": "repository",
            "execution_mode": "read_only",
            "require_ai_os_review": True,
        },
        initiator="chat_model",
        allowed_permissions=frozenset({"agent_delegation"}),
    )

    assert result.result["status"] == "VERIFIED"
    audit_arguments = repository.create_running.await_args.args[4]
    assert "inspect private repository state" not in audit_arguments
    assert json.loads(audit_arguments)["request_sha256"]
    audit_result = repository.finish.await_args.kwargs["result_json"]
    assert "private task result" not in audit_result
    assert json.loads(audit_result)["result_redacted"] is True
    gateway.delegate.assert_awaited_once()


@pytest.mark.asyncio
async def test_dex_write_request_from_chat_fails_closed_after_durable_audit():
    owner_id = uuid4()
    running = _execution(
        owner_id,
        tool_name="dex.delegate",
        permission="agent_delegation",
        initiator="chat_model",
        arguments_json="{}",
    )
    failed = _execution(
        owner_id,
        status=ToolExecutionStatus.FAILED,
        tool_name="dex.delegate",
        permission="agent_delegation",
        initiator="chat_model",
        arguments_json="{}",
    )
    failed.id = running.id
    failed.error_code = "tool_execution_failed"
    repository = Mock(
        create_running=AsyncMock(return_value=running),
        finish=AsyncMock(return_value=failed),
        conversation_exists_for_owner=AsyncMock(return_value=True),
    )
    gateway = Mock(available=True)
    gateway.delegate = AsyncMock(
        side_effect=DexGatewayError("DEX workspace writes require an explicit user action")
    )
    service = ToolService(AsyncMock(spec=AsyncSession), dex_gateway=gateway)
    service.repository = repository

    result = await service.execute_for_owner(
        owner_id,
        "dex.delegate",
        {
            "request": "write",
            "scope": "owner_workspace",
            "execution_mode": "workspace_write",
        },
        initiator="chat_model",
        allowed_permissions=frozenset({"agent_delegation"}),
    )

    assert result.status is ToolExecutionStatus.FAILED
    assert result.error_code == "tool_execution_failed"


@pytest.mark.asyncio
async def test_filesystem_write_content_and_read_content_are_redacted_from_audit(
    tmp_path,
):
    from app.services.filesystem_tool import OwnerFilesystemWorkspace

    owner_id = uuid4()
    root = tmp_path / "root"
    root.mkdir()
    workspace = OwnerFilesystemWorkspace((root,))
    session = AsyncMock(spec=AsyncSession)
    repository = Mock(
        conversation_exists_for_owner=AsyncMock(return_value=True),
    )
    captured_results: list[str] = []

    async def create_running(
        owner, conversation, tool_name, permission, arguments_json, *, initiator
    ):
        return _execution(
            owner,
            tool_name=tool_name,
            permission=permission,
            initiator=initiator,
            arguments_json=arguments_json,
        )

    async def finish(
        owner, execution_id, status, duration_ms, *, result_json=None, error_code=None
    ):
        if result_json is not None:
            captured_results.append(result_json)
        completed = _execution(
            owner,
            status=status,
            result=json.loads(result_json) if result_json is not None else None,
            tool_name="filesystem.read" if len(captured_results) == 2 else "filesystem.write",
            permission="workspace_read" if len(captured_results) == 2 else "workspace_write",
            initiator="chat_model",
            arguments_json="{}",
        )
        completed.id = execution_id
        completed.duration_ms = duration_ms
        completed.error_code = error_code
        return completed

    repository.create_running = AsyncMock(side_effect=create_running)
    repository.finish = AsyncMock(side_effect=finish)
    service = ToolService(session, filesystem_workspace=workspace)
    service.repository = repository

    written = await service.execute_for_owner(
        owner_id,
        "filesystem.write",
        {"path": "result.txt", "content": "private but non-secret content"},
        initiator="chat_model",
        allowed_permissions=frozenset({"workspace_write"}),
    )
    read = await service.execute_for_owner(
        owner_id,
        "filesystem.read",
        {"path": "result.txt"},
        initiator="chat_verifier",
        allowed_permissions=frozenset({"workspace_read"}),
    )

    write_audit_arguments = repository.create_running.await_args_list[0].args[4]
    assert "private but non-secret content" not in write_audit_arguments
    assert written.result["status"] == "completed"
    assert read.result["content"] == "private but non-secret content"
    assert "private but non-secret content" not in captured_results[1]
    assert json.loads(captured_results[1])["content_redacted"] is True


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
