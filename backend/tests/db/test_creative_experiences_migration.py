import app.models  # noqa: F401
from app.db.base import Base
from tests.db.test_initial_domain_migration import (
    RecordingOperations,
    _load_revision,
    _revision_path,
    _table_signature,
)
from tests.db.test_learning_system_migration import _upgrade_through_finance


REVISION_FILENAME = "0017_creative_experiences.py"


def _upgrade_through_learning(operations: RecordingOperations) -> None:
    _upgrade_through_finance(operations)
    _load_revision(operations, "0016_learning_system.py").upgrade()


def test_creative_revision_follows_learning():
    operations = RecordingOperations()
    revision = _load_revision(operations, REVISION_FILENAME)

    assert _revision_path(REVISION_FILENAME).name == REVISION_FILENAME
    assert revision.revision == "0017_creative_experiences"
    assert revision.down_revision == "0016_learning_system"
    assert revision.branch_labels is None
    assert revision.depends_on is None


def test_upgrade_matches_exact_creative_orm_schema():
    operations = RecordingOperations()
    _upgrade_through_learning(operations)
    operations.events.clear()

    _load_revision(operations, REVISION_FILENAME).upgrade()

    assert operations.events == [
        ("create_table", "creative_experiences"),
        ("create_table", "creative_turns"),
        ("create_index", "ix_creative_experiences_owner_updated_at"),
        ("create_index", "ix_creative_turns_experience_position"),
    ]
    for table_name in ("creative_experiences", "creative_turns"):
        assert _table_signature(operations.metadata.tables[table_name]) == _table_signature(
            Base.metadata.tables[table_name]
        )


def test_creative_downgrade_is_exact_reverse_dependency_order():
    operations = RecordingOperations()
    revision = _load_revision(operations, REVISION_FILENAME)

    revision.downgrade()

    assert operations.events == [
        ("drop_index", "ix_creative_turns_experience_position"),
        ("drop_index", "ix_creative_experiences_owner_updated_at"),
        ("drop_table", "creative_turns"),
        ("drop_table", "creative_experiences"),
    ]
