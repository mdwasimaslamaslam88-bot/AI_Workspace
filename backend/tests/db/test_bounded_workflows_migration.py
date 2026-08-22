import app.models  # noqa: F401
from app.db.base import Base
from tests.db.test_initial_domain_migration import (
    RecordingOperations,
    _load_revision,
    _revision_path,
    _table_signature,
)

REVISION_FILENAME = "0008_bounded_workflows.py"


def _upgrade_predecessors(operations: RecordingOperations) -> None:
    for filename in (
        "0001_initial_domain.py",
        "0002_user_access_credential.py",
        "0003_bound_message_content.py",
        "0004_owned_assets.py",
        "0005_document_intelligence.py",
        "0006_personal_memory.py",
        "0007_bounded_tools.py",
    ):
        _load_revision(operations, filename).upgrade()


def test_bounded_workflows_revision_follows_bounded_tools():
    operations = RecordingOperations()
    revision = _load_revision(operations, REVISION_FILENAME)

    assert _revision_path(REVISION_FILENAME).name == REVISION_FILENAME
    assert revision.revision == "0008_bounded_workflows"
    assert revision.down_revision == "0007_bounded_tools"
    assert revision.branch_labels is None
    assert revision.depends_on is None


def test_upgrade_matches_exact_workflow_orm_schema():
    operations = RecordingOperations()
    _upgrade_predecessors(operations)
    operations.events.clear()

    _load_revision(operations, REVISION_FILENAME).upgrade()

    assert operations.events == [
        ("drop_constraint", "ck_tool_executions_initiator_allowed"),
        ("create_check_constraint", "ck_tool_executions_initiator_allowed"),
        ("create_table", "workflows"),
        ("create_table", "workflow_steps"),
        ("create_index", "ix_workflows_owner_created_at"),
        ("create_index", "ix_workflows_owner_status"),
        ("create_index", "ix_workflow_steps_owner_workflow_position"),
    ]
    for table_name in ("workflows", "workflow_steps"):
        assert _table_signature(operations.metadata.tables[table_name]) == (
            _table_signature(Base.metadata.tables[table_name])
        )
    assert _table_signature(operations.metadata.tables["tool_executions"]) == (
        _table_signature(Base.metadata.tables["tool_executions"])
    )


def test_downgrade_removes_workflow_relations_in_dependency_order():
    operations = RecordingOperations()
    _upgrade_predecessors(operations)
    revision = _load_revision(operations, REVISION_FILENAME)
    revision.upgrade()
    operations.events.clear()

    revision.downgrade()

    assert operations.events == [
        ("drop_index", "ix_workflow_steps_owner_workflow_position"),
        ("drop_index", "ix_workflows_owner_status"),
        ("drop_index", "ix_workflows_owner_created_at"),
        ("drop_table", "workflow_steps"),
        ("drop_table", "workflows"),
        ("drop_constraint", "ck_tool_executions_initiator_allowed"),
        (
            "execute",
            "UPDATE tool_executions SET initiator = 'explicit_user' "
            "WHERE initiator = 'workflow'",
        ),
        ("create_check_constraint", "ck_tool_executions_initiator_allowed"),
    ]
    assert "tool_executions" in operations.metadata.tables
