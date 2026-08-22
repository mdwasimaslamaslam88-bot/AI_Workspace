import app.models  # noqa: F401
from app.db.base import Base
from tests.db.test_initial_domain_migration import (
    RecordingOperations,
    _load_revision,
    _revision_path,
    _table_signature,
)

REVISION_FILENAME = "0007_bounded_tools.py"


def _upgrade_predecessors(operations: RecordingOperations) -> None:
    for filename in (
        "0001_initial_domain.py",
        "0002_user_access_credential.py",
        "0003_bound_message_content.py",
        "0004_owned_assets.py",
        "0005_document_intelligence.py",
        "0006_personal_memory.py",
    ):
        _load_revision(operations, filename).upgrade()


def test_bounded_tools_revision_follows_personal_memory():
    operations = RecordingOperations()
    revision = _load_revision(operations, REVISION_FILENAME)

    assert _revision_path(REVISION_FILENAME).name == REVISION_FILENAME
    assert revision.revision == "0007_bounded_tools"
    assert revision.down_revision == "0006_personal_memory"
    assert revision.branch_labels is None
    assert revision.depends_on is None


def test_upgrade_matches_exact_tool_execution_orm_schema():
    operations = RecordingOperations()
    _upgrade_predecessors(operations)
    operations.events.clear()

    _load_revision(operations, REVISION_FILENAME).upgrade()

    assert operations.events == [
        ("create_table", "tool_executions"),
        ("create_index", "ix_tool_executions_owner_started_at"),
        ("create_index", "ix_tool_executions_owner_conversation"),
    ]
    assert _table_signature(operations.metadata.tables["tool_executions"]) == (
        _table_signature(Base.metadata.tables["tool_executions"])
    )


def test_downgrade_removes_only_tool_execution_history():
    operations = RecordingOperations()
    _upgrade_predecessors(operations)
    revision = _load_revision(operations, REVISION_FILENAME)
    revision.upgrade()
    operations.events.clear()

    revision.downgrade()

    assert operations.events == [
        ("drop_index", "ix_tool_executions_owner_conversation"),
        ("drop_index", "ix_tool_executions_owner_started_at"),
        ("drop_table", "tool_executions"),
    ]
    assert "memories" in operations.metadata.tables
