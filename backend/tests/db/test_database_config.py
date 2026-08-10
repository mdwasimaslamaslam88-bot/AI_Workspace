import os
from unittest.mock import Mock

import pytest

from app.core.config import Settings
from tests.db import conftest as database_fixtures
from tests.db.conftest import (
    _alembic_test_environment,
    _gated_test_database_settings,
    _integration_tests_selected,
    _is_explicitly_enabled,
    _validated_test_database_url,
)


def _selected_integration_config() -> Mock:
    config = Mock()
    config.getoption.return_value = "integration"
    return config


def _assert_gate_failure_has_no_database_side_effects(
    monkeypatch,
    *,
    expected_exception: type[BaseException],
) -> BaseException:
    engine = Mock()
    engine.connect = Mock()
    engine_factory = Mock(return_value=engine)
    alembic_command = Mock()
    alembic_config = Mock()
    alembic_dependencies = Mock(
        return_value=(alembic_command, alembic_config),
    )
    monkeypatch.setattr(
        database_fixtures,
        "create_async_engine",
        engine_factory,
    )
    monkeypatch.setattr(
        database_fixtures,
        "_alembic_dependencies",
        alembic_dependencies,
    )

    with pytest.raises(expected_exception) as caught:
        _gated_test_database_settings(_selected_integration_config())

    engine_factory.assert_not_called()
    engine.connect.assert_not_called()
    alembic_dependencies.assert_not_called()
    alembic_config.assert_not_called()
    alembic_command.downgrade.assert_not_called()
    alembic_command.upgrade.assert_not_called()
    alembic_command.run_migrations.assert_not_called()
    return caught.value


def test_database_integration_tests_require_exact_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("RUN_DATABASE_INTEGRATION_TESTS", raising=False)
    assert not _is_explicitly_enabled()

    monkeypatch.setenv("RUN_DATABASE_INTEGRATION_TESTS", "yes")
    assert not _is_explicitly_enabled()

    monkeypatch.setenv("RUN_DATABASE_INTEGRATION_TESTS", "true")
    assert _is_explicitly_enabled()


def test_integration_tests_require_explicit_marker_selection():
    config = Mock()
    config.getoption.return_value = ""
    assert not _integration_tests_selected(config)

    config.getoption.return_value = "integration"
    assert _integration_tests_selected(config)


def test_test_database_must_not_identify_runtime_database():
    configured = Settings(
        _env_file=None,
        DATABASE_URL="postgresql+asyncpg://app@127.0.0.1/ai_workspace_test",
        TEST_DATABASE_URL=(
            "postgresql+asyncpg://tester@127.0.0.1/ai_workspace_test"
        ),
    )

    with pytest.raises(pytest.fail.Exception, match="must not identify"):
        _validated_test_database_url(configured)


def test_test_database_requires_approved_name():
    configured = Settings(
        _env_file=None,
        DATABASE_URL="postgresql+asyncpg://127.0.0.1/workspace",
        TEST_DATABASE_URL="postgresql+asyncpg://127.0.0.1/workspace_test",
    )

    with pytest.raises(pytest.fail.Exception, match="database must be ai_workspace_test"):
        _validated_test_database_url(configured)


def test_test_database_requires_approved_host():
    configured = Settings(
        _env_file=None,
        DATABASE_URL="postgresql+asyncpg://127.0.0.1/workspace",
        TEST_DATABASE_URL="postgresql+asyncpg://localhost/ai_workspace_test",
    )

    with pytest.raises(pytest.fail.Exception, match="host must be 127.0.0.1"):
        _validated_test_database_url(configured)


def test_approved_test_database_is_accepted():
    configured = Settings(
        _env_file=None,
        DATABASE_URL="postgresql+asyncpg://127.0.0.1/workspace",
        TEST_DATABASE_URL=(
            "postgresql+asyncpg://127.0.0.1/ai_workspace_test"
        ),
    )

    assert _validated_test_database_url(configured) == str(
        configured.TEST_DATABASE_URL
    )


def test_missing_test_database_url_fails_closed():
    configured = Settings(
        _env_file=None,
        DATABASE_URL=None,
        TEST_DATABASE_URL=None,
    )

    with pytest.raises(pytest.fail.Exception, match="TEST_DATABASE_URL is required"):
        _validated_test_database_url(configured)


def test_alembic_test_environment_blanks_runtime_url_and_restores_environment(
    monkeypatch,
):
    test_url = "postgresql+asyncpg://127.0.0.1/ai_workspace_test"
    runtime_url = "postgresql+asyncpg://127.0.0.1/workspace"
    monkeypatch.setenv("DATABASE_URL", runtime_url)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("RUN_DATABASE_INTEGRATION_TESTS", raising=False)

    with _alembic_test_environment(test_url):
        assert os.environ["RUN_DATABASE_INTEGRATION_TESTS"] == "true"
        assert os.environ["TEST_DATABASE_URL"] == test_url
        assert os.environ["DATABASE_URL"] == ""

    assert os.environ["DATABASE_URL"] == runtime_url
    assert "TEST_DATABASE_URL" not in os.environ
    assert "RUN_DATABASE_INTEGRATION_TESTS" not in os.environ


def test_disabled_gate_has_no_database_side_effects(monkeypatch):
    monkeypatch.setenv("RUN_DATABASE_INTEGRATION_TESTS", "false")
    monkeypatch.setenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://127.0.0.1/ai_workspace_test",
    )
    monkeypatch.setenv("DATABASE_URL", "")

    _assert_gate_failure_has_no_database_side_effects(
        monkeypatch,
        expected_exception=pytest.fail.Exception,
    )


def test_missing_test_database_url_gate_has_no_database_side_effects(monkeypatch):
    monkeypatch.setenv("RUN_DATABASE_INTEGRATION_TESTS", "true")
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "")

    _assert_gate_failure_has_no_database_side_effects(
        monkeypatch,
        expected_exception=pytest.fail.Exception,
    )


def test_invalid_test_database_target_gate_has_no_database_side_effects(monkeypatch):
    monkeypatch.setenv("RUN_DATABASE_INTEGRATION_TESTS", "true")
    monkeypatch.setenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://localhost/not_the_approved_test_database",
    )
    monkeypatch.setenv("DATABASE_URL", "")

    _assert_gate_failure_has_no_database_side_effects(
        monkeypatch,
        expected_exception=pytest.fail.Exception,
    )


def test_query_parameter_target_override_has_no_database_side_effects(monkeypatch):
    password = "credential-must-not-appear"
    monkeypatch.setenv("RUN_DATABASE_INTEGRATION_TESTS", "true")
    monkeypatch.setenv(
        "TEST_DATABASE_URL",
        f"postgresql+asyncpg://user:{password}@127.0.0.1:5432/"
        "ai_workspace_test?host=somewhere",
    )
    monkeypatch.setenv("DATABASE_URL", "")

    failure = _assert_gate_failure_has_no_database_side_effects(
        monkeypatch,
        expected_exception=pytest.fail.Exception,
    )

    assert str(failure) == "TEST_DATABASE_URL must not include query parameters"
    assert password not in str(failure)


@pytest.mark.parametrize(
    "malformed_variable",
    ("TEST_DATABASE_URL", "DATABASE_URL"),
)
def test_malformed_database_url_failure_does_not_expose_credentials(
    monkeypatch,
    malformed_variable,
):
    password = "credential-must-not-appear"
    malformed_url = (
        f"postgresql+asyncpg://tester:{password}@127.0.0.1:not-a-port/"
        "ai_workspace_test"
    )
    monkeypatch.setenv("RUN_DATABASE_INTEGRATION_TESTS", "true")
    monkeypatch.setenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://127.0.0.1/ai_workspace_test",
    )
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv(malformed_variable, malformed_url)

    failure = _assert_gate_failure_has_no_database_side_effects(
        monkeypatch,
        expected_exception=pytest.fail.Exception,
    )

    assert str(failure) == "Database integration settings are invalid"
    assert password not in str(failure)
