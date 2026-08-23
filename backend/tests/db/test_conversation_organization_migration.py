import app.models  # noqa: F401
from app.db.base import Base
from tests.db.test_initial_domain_migration import (
    RecordingOperations,
    _load_revision,
    _revision_path,
    _table_signature,
)


REVISION_FILENAME = "0011_conversation_organization.py"


def _upgrade_predecessors(operations: RecordingOperations) -> None:
    for filename in (
        "0001_initial_domain.py",
        "0002_user_access_credential.py",
        "0003_bound_message_content.py",
        "0004_owned_assets.py",
        "0005_document_intelligence.py",
        "0006_personal_memory.py",
        "0007_bounded_tools.py",
        "0008_bounded_workflows.py",
        "0009_model_agnostic_document_embeddings.py",
        "0010_asset_provenance.py",
    ):
        _load_revision(operations, filename).upgrade()


def test_conversation_organization_revision_extends_single_chain():
    revision = _load_revision(RecordingOperations(), REVISION_FILENAME)

    assert _revision_path(REVISION_FILENAME).name == REVISION_FILENAME
    assert revision.revision == "0011_conversation_organization"
    assert revision.down_revision == "0010_asset_provenance"
    assert revision.branch_labels is None
    assert revision.depends_on is None


def test_conversation_organization_upgrade_matches_current_schema():
    operations = RecordingOperations()
    _upgrade_predecessors(operations)
    operations.events.clear()

    _load_revision(operations, REVISION_FILENAME).upgrade()

    assert operations.events == [
        ("add_column", "conversations.is_pinned"),
        ("add_column", "conversations.is_archived"),
        ("create_index", "ix_conversations_owner_archived_updated_at_id"),
        ("alter_column", "conversations.is_pinned"),
        ("alter_column", "conversations.is_archived"),
    ]
    assert _table_signature(operations.metadata.tables["conversations"]) == (
        _table_signature(Base.metadata.tables["conversations"])
    )


def test_conversation_organization_downgrade_removes_only_organization_state():
    operations = RecordingOperations()
    _upgrade_predecessors(operations)
    revision = _load_revision(operations, REVISION_FILENAME)
    revision.upgrade()
    operations.events.clear()

    revision.downgrade()

    assert operations.events == [
        ("drop_index", "ix_conversations_owner_archived_updated_at_id"),
        ("drop_column", "conversations.is_archived"),
        ("drop_column", "conversations.is_pinned"),
    ]
