import pytest

from app.core.config import Settings
from tests.db.conftest import _is_explicitly_enabled, _validated_test_database_url


def test_database_integration_tests_require_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("RUN_DATABASE_INTEGRATION_TESTS", raising=False)
    assert not _is_explicitly_enabled()

    monkeypatch.setenv("RUN_DATABASE_INTEGRATION_TESTS", "true")
    assert _is_explicitly_enabled()


def test_test_database_must_not_identify_runtime_database():
    configured = Settings(
        _env_file=None,
        DATABASE_URL="postgresql+asyncpg://app@localhost/workspace_test",
        TEST_DATABASE_URL="postgresql+asyncpg://tester@localhost/workspace_test",
    )

    with pytest.raises(pytest.fail.Exception, match="must not identify"):
        _validated_test_database_url(configured)


def test_test_database_requires_test_specific_name():
    configured = Settings(
        _env_file=None,
        DATABASE_URL="postgresql+asyncpg://localhost/workspace",
        TEST_DATABASE_URL="postgresql+asyncpg://localhost/integration",
    )

    with pytest.raises(pytest.fail.Exception, match="must start with test_ or end with _test"):
        _validated_test_database_url(configured)


def test_distinct_test_database_is_accepted():
    configured = Settings(
        _env_file=None,
        DATABASE_URL="postgresql+asyncpg://localhost/workspace",
        TEST_DATABASE_URL="postgresql+asyncpg://localhost/workspace_test",
    )

    assert _validated_test_database_url(configured) == str(configured.TEST_DATABASE_URL)
