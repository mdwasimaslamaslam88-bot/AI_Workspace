import app.models  # noqa: F401
from app.db.base import Base
from tests.db.test_initial_domain_migration import (
    RecordingOperations,
    _load_revision,
    _revision_path,
    _table_signature,
)
from tests.db.test_finance_intelligence_migration import _upgrade_through_marketing


REVISION_FILENAME = "0016_learning_system.py"


def _upgrade_through_finance(operations: RecordingOperations) -> None:
    _upgrade_through_marketing(operations)
    _load_revision(operations, "0015_finance_intelligence.py").upgrade()


def test_learning_revision_follows_finance():
    operations = RecordingOperations()
    revision = _load_revision(operations, REVISION_FILENAME)

    assert _revision_path(REVISION_FILENAME).name == REVISION_FILENAME
    assert revision.revision == "0016_learning_system"
    assert revision.down_revision == "0015_finance_intelligence"
    assert revision.branch_labels is None
    assert revision.depends_on is None


def test_upgrade_matches_exact_learning_orm_schema():
    operations = RecordingOperations()
    _upgrade_through_finance(operations)
    operations.events.clear()

    _load_revision(operations, REVISION_FILENAME).upgrade()

    assert operations.events == [
        ("create_table", "learning_programs"),
        ("create_table", "learning_lessons"),
        ("create_table", "learning_activities"),
        ("create_table", "learning_attempts"),
        ("create_table", "learning_review_items"),
        ("create_index", "ix_learning_programs_owner_updated_at"),
        ("create_index", "ix_learning_lessons_program_position"),
        ("create_index", "ix_learning_attempts_program_created_at"),
        ("create_index", "ix_learning_review_items_program_due_at"),
    ]
    for table_name in (
        "learning_programs",
        "learning_lessons",
        "learning_activities",
        "learning_attempts",
        "learning_review_items",
    ):
        assert _table_signature(operations.metadata.tables[table_name]) == _table_signature(
            Base.metadata.tables[table_name]
        )


def test_downgrade_removes_learning_relations_in_dependency_order():
    operations = RecordingOperations()
    _upgrade_through_finance(operations)
    revision = _load_revision(operations, REVISION_FILENAME)
    revision.upgrade()
    operations.events.clear()

    revision.downgrade()

    assert operations.events == [
        ("drop_index", "ix_learning_review_items_program_due_at"),
        ("drop_index", "ix_learning_attempts_program_created_at"),
        ("drop_index", "ix_learning_lessons_program_position"),
        ("drop_index", "ix_learning_programs_owner_updated_at"),
        ("drop_table", "learning_review_items"),
        ("drop_table", "learning_attempts"),
        ("drop_table", "learning_activities"),
        ("drop_table", "learning_lessons"),
        ("drop_table", "learning_programs"),
    ]
