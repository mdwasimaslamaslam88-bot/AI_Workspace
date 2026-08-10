import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

import app.models  # noqa: F401  # Populate the model registry.
from app.db.base import Base


VERSIONS_DIR = Path(__file__).parents[2] / "migrations" / "versions"


class RecordingOperations:
    def __init__(self) -> None:
        self.metadata = sa.MetaData()
        self.events: list[tuple[str, str]] = []

    @staticmethod
    def f(name: str) -> str:
        return name

    def create_table(self, table_name: str, *elements, **kwargs):
        table = sa.Table(table_name, self.metadata, *elements, **kwargs)
        self.events.append(("create_table", table_name))
        return table

    def create_index(
        self,
        index_name: str,
        table_name: str,
        columns,
        *,
        unique: bool = False,
        **kwargs,
    ):
        table = self.metadata.tables[table_name]
        expressions = [
            table.c[column] if isinstance(column, str) else column
            for column in columns
        ]
        index = sa.Index(index_name, *expressions, unique=unique, **kwargs)
        self.events.append(("create_index", index_name))
        return index

    def drop_index(self, index_name: str, **kwargs) -> None:
        self.events.append(("drop_index", index_name))

    def drop_table(self, table_name: str, **kwargs) -> None:
        self.events.append(("drop_table", table_name))


def _revision_path() -> Path:
    revision_files = sorted(VERSIONS_DIR.glob("*.py"))
    assert len(revision_files) == 1
    return revision_files[0]


def _load_revision(operations: RecordingOperations):
    alembic_module = ModuleType("alembic")
    alembic_module.op = operations
    spec = importlib.util.spec_from_file_location(
        "initial_domain_migration",
        _revision_path(),
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)

    with patch.dict(sys.modules, {"alembic": alembic_module}):
        spec.loader.exec_module(module)

    return module


def _normalized_sql(value) -> str:
    return " ".join(str(value).split())


def _constraint_signature(constraint) -> tuple:
    if isinstance(constraint, sa.PrimaryKeyConstraint):
        return (
            "primary_key",
            constraint.name,
            tuple(constraint.columns.keys()),
        )
    if isinstance(constraint, sa.ForeignKeyConstraint):
        return (
            "foreign_key",
            constraint.name,
            tuple(constraint.columns.keys()),
            tuple(element.target_fullname for element in constraint.elements),
            constraint.ondelete,
        )
    if isinstance(constraint, sa.UniqueConstraint):
        return (
            "unique",
            constraint.name,
            tuple(constraint.columns.keys()),
        )
    if isinstance(constraint, sa.CheckConstraint):
        return (
            "check",
            constraint.name,
            _normalized_sql(constraint.sqltext),
        )
    raise AssertionError(f"Unexpected constraint type: {type(constraint)!r}")


def _table_signature(table: sa.Table) -> tuple:
    dialect = postgresql.dialect()
    columns = tuple(
        (
            column.name,
            type(column.type).__name__,
            str(column.type.compile(dialect=dialect)),
            column.nullable,
            (
                None
                if column.server_default is None
                else _normalized_sql(column.server_default.arg)
            ),
        )
        for column in table.columns
    )
    constraints = tuple(
        sorted(
            (_constraint_signature(constraint) for constraint in table.constraints),
            key=repr,
        )
    )
    indexes = tuple(
        sorted(
            str(sa.schema.CreateIndex(index).compile(dialect=dialect))
            for index in table.indexes
        )
    )
    return columns, constraints, indexes


def test_initial_revision_is_the_single_root_revision():
    operations = RecordingOperations()
    revision = _load_revision(operations)

    assert _revision_path().name == "0001_initial_domain.py"
    assert revision.revision == "0001_initial_domain"
    assert revision.down_revision is None
    assert revision.branch_labels is None
    assert revision.depends_on is None

    revision.upgrade()

    assert operations.events == [
        ("create_table", "users"),
        ("create_table", "conversations"),
        ("create_index", "ix_conversations_owner_updated_at_id"),
        ("create_table", "messages"),
    ]


def test_initial_revision_schema_matches_base_metadata():
    operations = RecordingOperations()
    revision = _load_revision(operations)

    revision.upgrade()

    assert set(operations.metadata.tables) == set(Base.metadata.tables) == {
        "users",
        "conversations",
        "messages",
    }
    for table_name in Base.metadata.tables:
        assert _table_signature(operations.metadata.tables[table_name]) == (
            _table_signature(Base.metadata.tables[table_name])
        )


def test_initial_revision_downgrade_uses_safe_dependency_order():
    operations = RecordingOperations()
    revision = _load_revision(operations)
    revision.upgrade()
    operations.events.clear()

    revision.downgrade()

    assert operations.events == [
        ("drop_table", "messages"),
        ("drop_index", "ix_conversations_owner_updated_at_id"),
        ("drop_table", "conversations"),
        ("drop_table", "users"),
    ]
