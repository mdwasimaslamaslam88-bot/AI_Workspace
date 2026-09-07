import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
import threading
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tool import ToolExecution, ToolExecutionStatus
from app.services.filesystem_tool import OwnerFilesystemWorkspace
from app.services.tool import TOOL_REGISTRY, ToolService
import app.services.tool as tool_module


async def _observe(predicate, seconds=2):
    async with asyncio.timeout(seconds):
        while not predicate():
            await asyncio.sleep(0.002)


@pytest.fixture
def mutation(tmp_path):
    workspace = OwnerFilesystemWorkspace((tmp_path,))
    owner = uuid4()
    target = tmp_path / str(owner) / "result.txt"
    done = threading.Event()
    actual_write = workspace.write

    def tracked_write(*args, **kwargs):
        try:
            return actual_write(*args, **kwargs)
        finally:
            done.set()

    workspace.write = tracked_write
    execution = ToolExecution(
        id=uuid4(), owner_id=owner, tool_name="filesystem.write",
        permission="workspace_write", status=ToolExecutionStatus.RUNNING,
        initiator="dex_agent", arguments_json="{}", result_json=None,
        error_code=None, started_at=datetime.now(timezone.utc),
        completed_at=None, duration_ms=None,
    )
    audits = []

    async def finish(owner_id, execution_id, status, duration_ms, **kwargs):
        assert owner_id == owner and execution_id == execution.id
        audits.append({"status": status, "worker_done": done.is_set(),
                       "target_exists": target.exists(), **kwargs})
        execution.status = status
        execution.duration_ms = duration_ms
        execution.completed_at = datetime.now(timezone.utc)
        execution.result_json = kwargs.get("result_json")
        execution.error_code = kwargs.get("error_code")
        return execution

    service = ToolService(AsyncMock(spec=AsyncSession), filesystem_workspace=workspace)
    service.repository = Mock(create_running=AsyncMock(return_value=execution),
                              finish=AsyncMock(side_effect=finish))

    def start():
        return asyncio.create_task(service.execute_for_owner(
            owner, "filesystem.write", {"path": "result.txt", "content": "synthetic"},
            initiator="dex_agent", allowed_permissions=frozenset({"workspace_write"}),
        ))

    return workspace, owner, target, done, service, audits, start


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", ["cancel", "timeout"])
async def test_interrupted_lock_wait_exits_before_terminal_audit(mutation, monkeypatch, interruption):
    workspace, owner, target, done, service, audits, start = mutation
    entered = threading.Event()
    actual_write = workspace.write

    def entered_write(*args, **kwargs):
        entered.set()
        return actual_write(*args, **kwargs)

    workspace.write = entered_write
    if interruption == "timeout":
        monkeypatch.setitem(TOOL_REGISTRY, "filesystem.write", replace(
            TOOL_REGISTRY["filesystem.write"], timeout_seconds=0.06))
    workspace._write_lock.acquire()
    task = start()
    try:
        await _observe(entered.is_set)
        if interruption == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert (await task).status is ToolExecutionStatus.TIMED_OUT
        assert done.is_set(), "terminal outcome left the filesystem worker running"
        assert audits[0]["worker_done"] is True
        assert not target.exists()
        assert not target.parent.exists(), "cancelled lock waiter changed owner workspace"
    finally:
        workspace._write_lock.release()
        await _observe(done.is_set)
    assert not target.exists(), "worker published after its terminal audit"


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", ["cancel", "timeout"])
async def test_interruption_after_fsync_cleans_before_audit(mutation, monkeypatch, interruption):
    workspace, owner, target, done, service, audits, start = mutation
    entered, release = threading.Event(), threading.Event()
    real_fsync = os.fsync

    def gated_fsync(fd):
        real_fsync(fd)
        entered.set()
        assert release.wait(2)

    monkeypatch.setattr(os, "fsync", gated_fsync)
    if interruption == "timeout":
        monkeypatch.setitem(TOOL_REGISTRY, "filesystem.write", replace(
            TOOL_REGISTRY["filesystem.write"], timeout_seconds=0.04))
    task = start()
    try:
        await _observe(entered.is_set)
        if interruption == "cancel":
            task.cancel()
        await asyncio.sleep(0.08)
        assert not task.done(), "terminal outcome preceded syscall worker containment"
        assert audits == []
    finally:
        release.set()
        if interruption == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert (await task).status is ToolExecutionStatus.TIMED_OUT
        await _observe(done.is_set)
    assert audits[0]["worker_done"] is True
    assert not target.exists()
    assert not list(target.parent.glob(".ai-os-*"))


@pytest.mark.asyncio
async def test_already_published_effect_is_recorded_and_repeated_cancellation_propagates(
    mutation, monkeypatch,
):
    workspace, owner, target, done, service, audits, start = mutation
    entered, release = threading.Event(), threading.Event()
    real_unlink = os.unlink

    def gated_unlink(path, *args, **kwargs):
        if str(path).startswith(".ai-os-"):
            entered.set()
            assert release.wait(2)
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", gated_unlink)
    task = start()
    try:
        await _observe(entered.is_set)
        assert target.read_text() == "synthetic"
        task.cancel()
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done()
        assert audits == []
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await _observe(done.is_set)
    assert len(audits) == 1
    assert audits[0]["status"] is ToolExecutionStatus.COMPLETED
    assert audits[0]["worker_done"] is True
    result = json.loads(audits[0]["result_json"])
    assert result["cancellation_requested"] is True
    assert result["bytes_written"] == 9
    assert target.read_text() == "synthetic"
    assert not list(target.parent.glob(".ai-os-*"))


@pytest.mark.asyncio
async def test_stalled_syscall_retains_nonterminal_uncertainty(mutation, monkeypatch):
    workspace, owner, target, done, service, audits, start = mutation
    entered, release = threading.Event(), threading.Event()
    real_fsync = os.fsync

    def gated_fsync(fd):
        real_fsync(fd)
        entered.set()
        assert release.wait(2)

    monkeypatch.setattr(os, "fsync", gated_fsync)
    monkeypatch.setattr(tool_module, "FILESYSTEM_CLEANUP_SECONDS", 0.04, raising=False)
    task = start()
    try:
        await _observe(entered.is_set)
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as caught:
            await asyncio.wait_for(task, timeout=0.3)
        assert caught.value.execution_uncertain is True
        assert caught.value.cancellation_requested is True
        assert not done.is_set()
        assert audits == [], "uncontained worker was falsely assigned a safe terminal state"
    finally:
        release.set()
        await _observe(done.is_set)
    assert not target.exists()
    assert audits == [], "detached worker touched the caller's database session"


@pytest.mark.asyncio
async def test_repeated_cancellation_during_audit_keeps_one_completed_effect(mutation):
    workspace, owner, target, done, service, audits, start = mutation
    entered, release = asyncio.Event(), asyncio.Event()
    real_finish = service.repository.finish.side_effect

    async def blocked_finish(*args, **kwargs):
        entered.set()
        await release.wait()
        return await real_finish(*args, **kwargs)

    service.repository.finish.side_effect = blocked_finish
    task = start()
    await entered.wait()
    try:
        task.cancel()
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(audits) == 1
    assert audits[0]["status"] is ToolExecutionStatus.COMPLETED
    assert service.repository.finish.await_count == 1
    assert target.read_text() == "synthetic"


@pytest.mark.asyncio
@pytest.mark.parametrize("caller_cancelled", [False, True])
@pytest.mark.parametrize("audit_cancelled", [False, True])
async def test_failed_audit_after_publication_preserves_uncertainty(
    mutation, caller_cancelled, audit_cancelled,
):
    workspace, owner, target, done, service, audits, start = mutation
    entered, release = asyncio.Event(), asyncio.Event()

    async def failed_finish(*args, **kwargs):
        entered.set()
        await release.wait()
        if audit_cancelled:
            raise asyncio.CancelledError("synthetic audit cancellation")
        raise RuntimeError("synthetic audit storage unavailable")

    service.repository.finish.side_effect = failed_finish
    task = start()
    await entered.wait()
    assert target.read_text() == "synthetic"
    if caller_cancelled:
        task.cancel()
        await asyncio.sleep(0)
    release.set()
    expected = (tool_module.ToolExecutionUncertainCancellation if caller_cancelled
                else tool_module.ToolExecutionUncertainError)
    with pytest.raises(expected) as raised:
        await task
    assert raised.value.execution_uncertain is True
    assert raised.value.cancellation_requested is caller_cancelled
    assert audits == []
    assert service.repository.finish.await_count == 1
    assert target.read_text() == "synthetic"


@pytest.mark.asyncio
async def test_uncertain_workers_exhaust_capacity_before_enqueue_and_recover(mutation, monkeypatch):
    workspace, owner, target, done, service, audits, start = mutation
    release = threading.Event()
    entered = 0
    exited = 0
    counter_lock = threading.Lock()
    actual_write = workspace.write

    def stalled_write(*args, **kwargs):
        nonlocal entered, exited
        with counter_lock:
            entered += 1
        try:
            assert release.wait(2)
            return actual_write(*args, **kwargs)
        finally:
            with counter_lock:
                exited += 1

    workspace.write = stalled_write
    monkeypatch.setattr(tool_module, "FILESYSTEM_CLEANUP_SECONDS", 0.04)
    # Production callbacks share a workspace but own separate DB sessions.
    # Model that distinction while retaining an observable fake audit store.
    def start():
        record = ToolExecution(
            id=uuid4(), owner_id=owner, tool_name="filesystem.write",
            permission="workspace_write", status=ToolExecutionStatus.RUNNING,
            initiator="dex_agent", arguments_json="{}", result_json=None,
            error_code=None, started_at=datetime.now(timezone.utc),
            completed_at=None, duration_ms=None,
        )

        async def finish(owner_id, execution_id, status, duration_ms, **kwargs):
            assert owner_id == owner and execution_id == record.id
            audits.append({"status": status, **kwargs})
            record.status = status
            record.completed_at = datetime.now(timezone.utc)
            record.duration_ms = duration_ms
            record.error_code = kwargs.get("error_code")
            record.result_json = kwargs.get("result_json")
            return record

        child = ToolService(AsyncMock(spec=AsyncSession), filesystem_workspace=workspace)
        child.repository = Mock(create_running=AsyncMock(return_value=record),
                                finish=AsyncMock(side_effect=finish))
        return asyncio.create_task(child.execute_for_owner(
            owner, "filesystem.write", {"path": "result.txt", "content": "synthetic"},
            initiator="dex_agent", allowed_permissions=frozenset({"workspace_write"}),
        ))

    tasks = [start() for _ in range(4)]
    try:
        await _observe(lambda: entered == 4)
        for task in tasks:
            task.cancel()
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        assert all(isinstance(item, asyncio.CancelledError) for item in outcomes)
        assert audits == []
        rejected = await start()
        assert rejected.status is ToolExecutionStatus.FAILED
        assert rejected.error_code == "tool_unavailable"
        assert entered == 4, "exhausted admission still enqueued a fifth worker"
        assert exited == 0
    finally:
        release.set()
        await _observe(lambda: exited == 4)
    assert not target.exists()
    assert len(audits) == 1
    # Capacity becomes available only after actual worker exits.
    assert (await start()).status is ToolExecutionStatus.COMPLETED
    assert entered == 5


@pytest.mark.asyncio
async def test_timeout_during_uncontained_syscall_reports_uncertainty(mutation, monkeypatch):
    workspace, owner, target, done, service, audits, start = mutation
    entered, release = threading.Event(), threading.Event()
    real_fsync = os.fsync

    def stalled_fsync(fd):
        real_fsync(fd)
        entered.set()
        assert release.wait(2)

    monkeypatch.setattr(os, "fsync", stalled_fsync)
    monkeypatch.setattr(tool_module, "FILESYSTEM_CLEANUP_SECONDS", 0.03)
    monkeypatch.setitem(TOOL_REGISTRY, "filesystem.write", replace(
        TOOL_REGISTRY["filesystem.write"], timeout_seconds=0.03))
    task = start()
    try:
        await _observe(entered.is_set)
        with pytest.raises(RuntimeError, match="containment is uncertain"):
            await task
        assert audits == []
        assert not done.is_set()
    finally:
        release.set()
        await _observe(done.is_set)
    assert not target.exists()


@pytest.mark.asyncio
async def test_link_publication_window_rejects_alias_then_allows_verified_read(mutation, monkeypatch):
    workspace, owner, target, done, service, audits, start = mutation
    entered, release = threading.Event(), threading.Event()
    real_unlink = os.unlink

    def gated_unlink(path, *args, **kwargs):
        if str(path).startswith(".ai-os-"):
            entered.set()
            assert release.wait(2)
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", gated_unlink)
    task = start()
    try:
        await _observe(entered.is_set)
        assert target.stat().st_nlink == 2
        from app.services.filesystem_tool import FilesystemToolError
        with pytest.raises(FilesystemToolError, match="hardlink"):
            workspace.read(owner, "result.txt")
    finally:
        release.set()
        assert (await task).status is ToolExecutionStatus.COMPLETED
    assert target.stat().st_nlink == 1
    assert workspace.read(owner, "result.txt").payload["content"] == "synthetic"


@pytest.mark.asyncio
async def test_failed_readback_does_not_claim_completed_or_absent_effect(mutation, monkeypatch):
    workspace, owner, target, done, service, audits, start = mutation
    real_unlink = os.unlink

    def corrupt_after_cleanup(path, *args, **kwargs):
        result = real_unlink(path, *args, **kwargs)
        if str(path).startswith(".ai-os-"):
            target.write_text("synthetic local corruption")
        return result

    monkeypatch.setattr(os, "unlink", corrupt_after_cleanup)
    with pytest.raises(RuntimeError, match="published effect is uncertain"):
        await start()
    assert done.is_set()
    assert audits == []
    assert target.read_text() == "synthetic local corruption"


@pytest.mark.asyncio
async def test_cancellation_is_per_call_and_other_owner_can_finish(mutation):
    workspace, owner, target, done, service, audits, start = mutation
    other_owner = uuid4()
    from app.services.filesystem_tool import FilesystemWriteControl
    import time

    workspace._write_lock.acquire()
    cancelled_task = start()
    other_control = FilesystemWriteControl(time.monotonic() + 2)
    other_task = asyncio.create_task(asyncio.to_thread(
        workspace.write, other_owner, "other.txt", "other owner", control=other_control,
    ))
    try:
        await asyncio.sleep(0.02)
        cancelled_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled_task
        assert not other_task.done()
        assert not other_control.cancelled.is_set()
    finally:
        workspace._write_lock.release()
    assert (await other_task).payload["changed"] is True
    assert workspace.read(other_owner, "other.txt").payload["content"] == "other owner"
    assert not target.exists()


@pytest.mark.asyncio
async def test_write_capacity_rejects_before_fifth_worker_starts(mutation):
    workspace, owner, target, done, service, audits, start = mutation
    release = threading.Event()
    entered = 0
    counter_lock = threading.Lock()
    actual_write = workspace.write

    def waiting_write(*args, **kwargs):
        nonlocal entered
        with counter_lock:
            entered += 1
        assert release.wait(2)
        return actual_write(*args, **kwargs)

    workspace.write = waiting_write
    tasks = [start() for _ in range(4)]
    try:
        await _observe(lambda: entered == 4)
        fifth = start()
        tasks.append(fifth)
        await asyncio.sleep(0.04)
        assert fifth.done(), "fifth request was queued despite four active workers"
        assert fifth.result().status is ToolExecutionStatus.FAILED
        assert entered == 4
    finally:
        release.set()
        await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_process_wide_task_cancellation_cannot_fake_thread_completion(mutation, monkeypatch):
    workspace, owner, target, done, service, audits, start = mutation
    entered, release = threading.Event(), threading.Event()
    real_fsync = os.fsync

    def gated_fsync(fd):
        real_fsync(fd)
        entered.set()
        assert release.wait(2)

    monkeypatch.setattr(os, "fsync", gated_fsync)
    task = start()
    try:
        await _observe(entered.is_set)
        # asyncio.run shutdown cancels Tasks, including a to_thread wrapper,
        # but a bare executor Future is not one of those Tasks.
        for pending in tuple(tool_module._RETAINED_FILESYSTEM_TASKS):
            if isinstance(pending, asyncio.Task):
                pending.cancel()
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done(), "cancelled wrapper was mistaken for a stopped thread"
        assert audits == []
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await _observe(done.is_set)
    assert not target.exists()
    assert audits[0]["worker_done"] is True
