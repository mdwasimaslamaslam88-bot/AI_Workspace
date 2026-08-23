import subprocess
import sys
from unittest.mock import Mock, call

from scripts.disposable_database_environment import (
    EPHEMERAL_TEST_DATABASE_URL,
    apply_ephemeral_test_database_override,
)
from scripts.run_runtime_e2e import PROJECT_ROOT, run_runtime_modules


def test_ephemeral_override_preserves_loaded_application_database():
    application_url = "postgresql+asyncpg://app@127.0.0.1:65432/ai_workspace"
    configured_test_url = (
        "postgresql+asyncpg://app@127.0.0.1:65432/ai_workspace_test"
    )
    ephemeral_url = (
        "postgresql+asyncpg://work_station_test_admin@127.0.0.1:43210/"
        "ai_workspace_test"
    )

    environment = apply_ephemeral_test_database_override(
        {
            "DATABASE_URL": application_url,
            "TEST_DATABASE_URL": configured_test_url,
            EPHEMERAL_TEST_DATABASE_URL: ephemeral_url,
        }
    )

    assert environment["DATABASE_URL"] == application_url
    assert environment["TEST_DATABASE_URL"] == ephemeral_url
    assert EPHEMERAL_TEST_DATABASE_URL not in environment


def test_blank_ephemeral_override_is_removed_without_changing_test_database():
    environment = apply_ephemeral_test_database_override(
        {
            "TEST_DATABASE_URL": "postgresql+asyncpg://127.0.0.1/test",
            EPHEMERAL_TEST_DATABASE_URL: "  ",
        }
    )

    assert environment == {
        "TEST_DATABASE_URL": "postgresql+asyncpg://127.0.0.1/test"
    }


def test_runtime_runner_uses_only_fixed_python_modules_and_child_environment():
    runner = Mock(return_value=subprocess.CompletedProcess([], 0))
    environment = {"SAFE_SETTING": "value"}

    run_runtime_modules(
        environment,
        modules=("scripts.first_smoke", "scripts.second_smoke"),
        runner=runner,
    )

    assert runner.call_args_list == [
        call(
            [sys.executable, "-m", "scripts.first_smoke"],
            cwd=PROJECT_ROOT,
            env={"SAFE_SETTING": "value"},
            check=True,
        ),
        call(
            [sys.executable, "-m", "scripts.second_smoke"],
            cwd=PROJECT_ROOT,
            env={"SAFE_SETTING": "value"},
            check=True,
        ),
    ]
