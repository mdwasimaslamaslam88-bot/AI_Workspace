import app.models  # noqa: F401  # Populate the model registry.
from app.db.base import Base
from tests.db.test_initial_domain_migration import (
    RecordingOperations,
    _conversation_signature_before_organization,
    _load_revision,
    _revision_path,
    _table_signature,
)


REVISION_FILENAME = "0003_bound_message_content.py"
CONSTRAINT_NAME = "ck_messages_content_length_bounded"


def _upgrade_predecessors(operations: RecordingOperations) -> None:
    _load_revision(operations).upgrade()
    _load_revision(
        operations,
        "0002_user_access_credential.py",
    ).upgrade()


def test_message_content_length_revision_follows_credential_revision():
    operations = RecordingOperations()
    revision = _load_revision(operations, REVISION_FILENAME)

    assert _revision_path(REVISION_FILENAME).name == REVISION_FILENAME
    assert revision.revision == "0003_bound_message_content"
    assert revision.down_revision == "0002_user_access_credential"
    assert revision.branch_labels is None
    assert revision.depends_on is None


def test_upgrade_adds_only_the_bounded_message_content_check():
    operations = RecordingOperations()
    _upgrade_predecessors(operations)
    revision = _load_revision(operations, REVISION_FILENAME)
    operations.events.clear()

    revision.upgrade()

    assert operations.events == [
        ("create_check_constraint", CONSTRAINT_NAME),
    ]
    assert set(operations.metadata.tables) == {"users", "conversations", "messages"}
    for table_name, migrated_table in operations.metadata.tables.items():
        migrated_signature = _table_signature(
            migrated_table
        )
        current_signature = _table_signature(Base.metadata.tables[table_name])
        if table_name == "users":
            assert set(migrated_signature[0]) == set(current_signature[0])
            assert migrated_signature[1:] == current_signature[1:]
        elif table_name == "conversations":
            assert migrated_signature == _conversation_signature_before_organization(
                Base.metadata.tables[table_name]
            )
        else:
            assert migrated_signature == current_signature


def test_downgrade_removes_only_the_bounded_message_content_check():
    operations = RecordingOperations()
    _upgrade_predecessors(operations)
    revision = _load_revision(operations, REVISION_FILENAME)
    predecessor_signature = _table_signature(
        operations.metadata.tables["messages"]
    )
    revision.upgrade()
    operations.events.clear()

    revision.downgrade()

    assert operations.events == [("drop_constraint", CONSTRAINT_NAME)]
    assert _table_signature(operations.metadata.tables["messages"]) == (
        predecessor_signature
    )


def test_revision_contains_no_message_rewrite_or_truncation():
    source = _revision_path(REVISION_FILENAME).read_text(encoding="utf-8")
    normalized = source.upper()

    assert "UPDATE " not in normalized
    assert "TRUNCATE " not in normalized
    assert "SUBSTRING" not in normalized
    assert "LEFT(" not in normalized
