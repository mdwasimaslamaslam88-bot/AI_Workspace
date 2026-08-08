import os

import pytest
from sqlalchemy.engine import make_url

from app.core.config import Settings


def _database_identity(value: str) -> tuple[str, str | None, int, str | None]:
    url = make_url(value)
    return (url.drivername, url.host, url.port or 5432, url.database)


def _is_explicitly_enabled() -> bool:
    value = os.getenv("RUN_DATABASE_INTEGRATION_TESTS", "")
    return value.strip().lower() in {"1", "true", "yes"}


def _validated_test_database_url(configured: Settings) -> str:
    if configured.TEST_DATABASE_URL is None:
        pytest.fail("TEST_DATABASE_URL is required when database integration tests are enabled")
    value = str(configured.TEST_DATABASE_URL)
    test_url = make_url(value)
    database_name = test_url.database or ""
    if not (database_name.startswith("test_") or database_name.endswith("_test")):
        pytest.fail("TEST_DATABASE_URL database name must start with test_ or end with _test")

    if configured.DATABASE_URL is not None:
        runtime_value = str(configured.DATABASE_URL)
        if _database_identity(value) == _database_identity(runtime_value):
            pytest.fail("TEST_DATABASE_URL must not identify the DATABASE_URL database")

    return value


@pytest.fixture(scope="session")
def test_database_url():
    if not _is_explicitly_enabled():
        pytest.skip("Database integration tests require RUN_DATABASE_INTEGRATION_TESTS=true")

    configured = Settings(_env_file=None)
    return _validated_test_database_url(configured)
