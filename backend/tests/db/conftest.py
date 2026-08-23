import os
import re
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

if TYPE_CHECKING:
    from app.core.config import Settings


PROJECT_ROOT = Path(__file__).parents[2]
APPROVED_TEST_DATABASE_HOST = "127.0.0.1"
APPROVED_TEST_DATABASE_NAME = "ai_workspace_test"


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: requires the explicitly approved disposable PostgreSQL database",
    )


@pytest.fixture(autouse=True)
def isolate_process_local_edge_rate_limits():
    """ASGITransport does not emit lifespan events between integration tests."""

    from app.main import app
    from app.middleware.edge_rate_limit import EdgeRateLimitMiddleware

    if app.middleware_stack is None:
        app.middleware_stack = app.build_middleware_stack()
    middleware = app.middleware_stack
    while middleware is not None:
        if isinstance(middleware, EdgeRateLimitMiddleware):
            middleware._clear()
            break
        middleware = getattr(middleware, "app", None)
    yield
    if isinstance(middleware, EdgeRateLimitMiddleware):
        middleware._clear()


def _integration_tests_selected(config) -> bool:
    mark_expression = config.getoption("-m") or ""
    return re.search(r"\bintegration\b", mark_expression) is not None


def _database_identity(value: str) -> tuple[str, str | None, int, str | None]:
    url = make_url(value)
    return (url.drivername, url.host, url.port or 5432, url.database)


def _is_explicitly_enabled() -> bool:
    value = os.getenv("RUN_DATABASE_INTEGRATION_TESTS", "")
    return value.strip().lower() == "true"


def _assert_approved_test_database_url(value: str) -> None:
    test_url = make_url(value)
    if test_url.query:
        pytest.fail("TEST_DATABASE_URL must not include query parameters")
    if test_url.drivername != "postgresql+asyncpg":
        pytest.fail("TEST_DATABASE_URL must use the postgresql+asyncpg driver")
    if test_url.host != APPROVED_TEST_DATABASE_HOST:
        pytest.fail(
            f"TEST_DATABASE_URL host must be {APPROVED_TEST_DATABASE_HOST}"
        )
    if test_url.database != APPROVED_TEST_DATABASE_NAME:
        pytest.fail(
            f"TEST_DATABASE_URL database must be {APPROVED_TEST_DATABASE_NAME}"
        )


def _validated_test_database_url(configured: "Settings") -> str:
    if configured.TEST_DATABASE_URL is None:
        pytest.fail(
            "TEST_DATABASE_URL is required when database integration tests are enabled"
        )

    value = str(configured.TEST_DATABASE_URL)
    _assert_approved_test_database_url(value)

    if configured.DATABASE_URL is not None:
        runtime_value = str(configured.DATABASE_URL)
        if _database_identity(value) == _database_identity(runtime_value):
            pytest.fail("TEST_DATABASE_URL must not identify the DATABASE_URL database")

    return value


def _load_test_database_settings() -> "Settings":
    try:
        from app.core.config import Settings

        return Settings(_env_file=None)
    except (ValidationError, SettingsError):
        pytest.fail(
            "Database integration settings are invalid",
            pytrace=False,
        )


def _gated_test_database_settings(pytestconfig) -> "Settings":
    if not _integration_tests_selected(pytestconfig):
        pytest.skip("PostgreSQL integration tests require explicit -m integration")
    if not _is_explicitly_enabled():
        pytest.fail(
            "RUN_DATABASE_INTEGRATION_TESTS=true is required for integration tests"
        )

    configured = _load_test_database_settings()
    _validated_test_database_url(configured)
    return configured


@contextmanager
def _alembic_test_environment(test_database_url: str) -> Iterator[None]:
    keys = (
        "RUN_DATABASE_INTEGRATION_TESTS",
        "TEST_DATABASE_URL",
        "DATABASE_URL",
    )
    original = {key: os.environ.get(key) for key in keys}
    os.environ["RUN_DATABASE_INTEGRATION_TESTS"] = "true"
    os.environ["TEST_DATABASE_URL"] = test_database_url
    os.environ["DATABASE_URL"] = ""
    try:
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture(scope="session")
def test_database_settings(pytestconfig) -> "Settings":
    return _gated_test_database_settings(pytestconfig)


@pytest.fixture(scope="session")
def test_database_url(test_database_settings: "Settings") -> str:
    return _validated_test_database_url(test_database_settings)


def _alembic_dependencies():
    from alembic import command
    from alembic.config import Config

    return command, Config


@pytest.fixture(scope="session")
def migrated_test_database(test_database_url: str) -> Iterator[str]:
    command, Config = _alembic_dependencies()

    alembic_config = Config(str(PROJECT_ROOT / "alembic.ini"))
    _assert_approved_test_database_url(test_database_url)

    with _alembic_test_environment(test_database_url):
        command.downgrade(alembic_config, "base")
        try:
            _assert_approved_test_database_url(test_database_url)
            command.upgrade(alembic_config, "head")
            yield test_database_url
        finally:
            _assert_approved_test_database_url(test_database_url)
            command.downgrade(alembic_config, "base")


@pytest_asyncio.fixture
async def test_database_engine(
    migrated_test_database: str,
    test_database_settings: "Settings",
) -> AsyncIterator[AsyncEngine]:
    _assert_approved_test_database_url(migrated_test_database)
    from app.clients.postgres import build_postgres_connect_args

    engine = create_async_engine(
        migrated_test_database,
        pool_pre_ping=True,
        poolclass=NullPool,
        connect_args=build_postgres_connect_args(test_database_settings),
    )
    try:
        yield engine
    finally:
        await engine.dispose()
