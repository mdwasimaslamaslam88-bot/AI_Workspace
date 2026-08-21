import app.models  # noqa: F401  # Populate the model registry.
from app.db.base import Base
from tests.db.test_initial_domain_migration import (
    RecordingOperations,
    _load_revision,
    _revision_path,
    _table_signature,
)


REVISION_FILENAME = "0004_owned_assets.py"


def _upgrade_predecessors(operations: RecordingOperations) -> None:
    for filename in (
        "0001_initial_domain.py",
        "0002_user_access_credential.py",
        "0003_bound_message_content.py",
    ):
        _load_revision(operations, filename).upgrade()


def test_owned_assets_revision_follows_message_content_revision():
    operations = RecordingOperations()
    revision = _load_revision(operations, REVISION_FILENAME)

    assert _revision_path(REVISION_FILENAME).name == REVISION_FILENAME
    assert revision.revision == "0004_owned_assets"
    assert revision.down_revision == "0003_bound_message_content"
    assert revision.branch_labels is None
    assert revision.depends_on is None


def test_upgrade_creates_exact_asset_and_message_asset_schema():
    operations = RecordingOperations()
    _upgrade_predecessors(operations)
    operations.events.clear()

    _load_revision(operations, REVISION_FILENAME).upgrade()

    assert operations.events == [
        ("create_table", "assets"),
        ("create_index", "ix_assets_owner_created_at_id"),
        ("create_index", "ix_assets_deleted_at"),
        ("create_table", "message_assets"),
    ]
    assert set(operations.metadata.tables) == {
        "users",
        "conversations",
        "messages",
        "assets",
        "message_assets",
    }
    assert _table_signature(operations.metadata.tables["assets"]) == (
        _table_signature(Base.metadata.tables["assets"])
    )
    assert _table_signature(operations.metadata.tables["message_assets"]) == (
        _table_signature(Base.metadata.tables["message_assets"])
    )


def test_downgrade_drops_relation_before_assets_and_preserves_old_tables():
    operations = RecordingOperations()
    _upgrade_predecessors(operations)
    revision = _load_revision(operations, REVISION_FILENAME)
    revision.upgrade()
    operations.events.clear()

    revision.downgrade()

    assert operations.events == [
        ("drop_table", "message_assets"),
        ("drop_index", "ix_assets_deleted_at"),
        ("drop_index", "ix_assets_owner_created_at_id"),
        ("drop_table", "assets"),
    ]
    assert all(
        table_name in operations.metadata.tables
        for table_name in ("users", "conversations", "messages")
    )
