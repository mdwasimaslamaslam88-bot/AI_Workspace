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

    def add_column(self, table_name: str, column: sa.Column, **kwargs) -> None:
        self.metadata.tables[table_name].append_column(column)
        self.events.append(("add_column", f"{table_name}.{column.name}"))

    def create_unique_constraint(
        self,
        constraint_name: str,
        table_name: str,
        columns,
        **kwargs,
    ) -> None:
        table = self.metadata.tables[table_name]
        sa.UniqueConstraint(
            *(table.c[column_name] for column_name in columns),
            name=constraint_name,
        )
        self.events.append(("create_unique_constraint", constraint_name))

    def create_check_constraint(
        self,
        constraint_name: str,
        table_name: str,
        condition,
        **kwargs,
    ) -> None:
        table = self.metadata.tables[table_name]
        constraint = sa.CheckConstraint(condition, name=constraint_name)
        table.append_constraint(constraint)
        self.events.append(("create_check_constraint", constraint_name))

    def create_foreign_key(
        self,
        constraint_name: str,
        source_table: str,
        referent_table: str,
        local_columns,
        remote_columns,
        **kwargs,
    ) -> None:
        table = self.metadata.tables[source_table]
        sa.ForeignKeyConstraint(
            [table.c[column_name] for column_name in local_columns],
            [f"{referent_table}.{column_name}" for column_name in remote_columns],
            name=constraint_name,
            ondelete=kwargs.get("ondelete"),
        )
        self.events.append(("create_foreign_key", constraint_name))

    def drop_constraint(
        self,
        constraint_name: str,
        table_name: str,
        **kwargs,
    ) -> None:
        table = self.metadata.tables[table_name]
        constraint = next(
            item
            for item in table.constraints
            if item.name == constraint_name
        )
        table.constraints.remove(constraint)
        self.events.append(("drop_constraint", constraint_name))

    def execute(self, statement, **kwargs) -> None:
        self.events.append(("execute", " ".join(str(statement).split())))
        return _RecordingScalarResult(0)

    def get_bind(self):
        return self

    def alter_column(self, table_name: str, column_name: str, **kwargs) -> None:
        column = self.metadata.tables[table_name].c[column_name]
        if "server_default" in kwargs:
            default = kwargs["server_default"]
            column.server_default = (
                None if default is None else sa.DefaultClause(sa.text(str(default)))
            )
        self.events.append(("alter_column", f"{table_name}.{column_name}"))

    def drop_column(self, table_name: str, column_name: str, **kwargs) -> None:
        table = self.metadata.tables[table_name]
        table._columns.remove(table.c[column_name])
        self.events.append(("drop_column", f"{table_name}.{column_name}"))


class _RecordingScalarResult:
    def __init__(self, value) -> None:
        self.value = value

    def scalar_one(self):
        return self.value


def _revision_path(filename: str = "0001_initial_domain.py") -> Path:
    revision_path = VERSIONS_DIR / filename
    assert revision_path.is_file()
    return revision_path


def _load_revision(
    operations: RecordingOperations,
    filename: str = "0001_initial_domain.py",
):
    alembic_module = ModuleType("alembic")
    alembic_module.op = operations
    spec = importlib.util.spec_from_file_location(
        f"migration_{filename.removesuffix('.py')}",
        _revision_path(filename),
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)

    with patch.dict(sys.modules, {"alembic": alembic_module}):
        spec.loader.exec_module(module)

    return module


def _normalized_sql(value) -> str:
    return " ".join(str(value).split())


def _column_signature(column: sa.Column) -> tuple:
    dialect = postgresql.dialect()
    return (
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
    columns = tuple(_column_signature(column) for column in table.columns)
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


def test_initial_revision_is_the_root_revision():
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


def test_initial_revision_preserves_original_domain_schema():
    operations = RecordingOperations()
    revision = _load_revision(operations)

    revision.upgrade()

    original_tables = {
        "users",
        "conversations",
        "messages",
    }
    assert set(operations.metadata.tables) == original_tables
    assert _table_signature(operations.metadata.tables["conversations"]) == (
        _table_signature(Base.metadata.tables["conversations"])
    )
    initial_message_signature = _table_signature(
        operations.metadata.tables["messages"]
    )
    current_message_signature = _table_signature(Base.metadata.tables["messages"])
    assert initial_message_signature[0] == current_message_signature[0]
    assert initial_message_signature[2] == current_message_signature[2]
    assert set(initial_message_signature[1]) == {
        constraint
        for constraint in current_message_signature[1]
        if constraint[1] != "ck_messages_content_length_bounded"
    }

    initial_users = operations.metadata.tables["users"]
    current_users = Base.metadata.tables["users"]
    assert tuple(initial_users.c.keys()) == ("id", "created_at", "updated_at")
    for column_name in initial_users.c.keys():
        assert _column_signature(initial_users.c[column_name]) == (
            _column_signature(current_users.c[column_name])
        )
    assert tuple(
        sorted(
            (
                _constraint_signature(constraint)
                for constraint in initial_users.constraints
            ),
            key=repr,
        )
    ) == (("primary_key", "pk_users", ("id",)),)


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
