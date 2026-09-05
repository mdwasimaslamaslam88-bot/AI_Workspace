import app.models  # noqa: F401
import sqlalchemy as sa
from app.db.base import Base
from tests.db.test_initial_domain_migration import (
    RecordingOperations,
    _load_revision,
    _revision_path,
    _table_signature,
)

REVISION_FILENAME = "0007_bounded_tools.py"

_HISTORICAL_TOOL_CONSTRAINTS = {
    "tool_name_allowed": (
        "tool_name IN ('calculator', 'local_time', 'document_search', "
        "'conversation_search', 'memory_search')"
    ),
    "permission_allowed": (
        "permission IN ('utility', 'personal_documents_read', "
        "'personal_conversations_read', 'personal_memory_read')"
    ),
    "initiator_allowed": "initiator = 'explicit_user'",
    "error_code_allowed": (
        "error_code IS NULL OR error_code IN ('tool_timed_out', "
        "'tool_cancelled', 'tool_execution_failed', 'tool_unavailable', "
        "'server_restarted')"
    ),
}


def _replace_historical_tool_constraints(
    table: sa.Table,
    *,
    initiator: str,
) -> None:
    constraints = dict(_HISTORICAL_TOOL_CONSTRAINTS)
    constraints["initiator_allowed"] = initiator
    for suffix, expression in constraints.items():
        name = f"ck_tool_executions_{suffix}"
        current = next(
            constraint
            for constraint in table.constraints
            if constraint.name == name
        )
        table.constraints.remove(current)
        table.append_constraint(
            sa.CheckConstraint(expression, name=suffix)
        )


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
    expected_metadata = sa.MetaData(naming_convention=Base.metadata.naming_convention)
    expected = Base.metadata.tables["tool_executions"].to_metadata(
        expected_metadata
    )
    _replace_historical_tool_constraints(
        expected,
        initiator="initiator = 'explicit_user'",
    )
    assert _table_signature(operations.metadata.tables["tool_executions"]) == (
        _table_signature(expected)
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
