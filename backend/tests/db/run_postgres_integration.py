import os
import sys
from pathlib import Path

from sqlalchemy.engine import make_url

PROJECT_ROOT = Path(__file__).parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.disposable_database_environment import load_backend_environment  # noqa: E402


def _database_identity(value: str) -> tuple[str | None, int, str | None]:
    parsed = make_url(value)
    return (parsed.host, parsed.port or 5432, parsed.database)


def _require_separate_application_and_test_databases(
    environment: dict[str, str],
) -> None:
    application_url = environment.get("DATABASE_URL", "").strip()
    test_url = environment.get("TEST_DATABASE_URL", "").strip()
    if not application_url or not test_url:
        raise RuntimeError(
            "DATABASE_URL and TEST_DATABASE_URL must both be configured"
        )
    if _database_identity(application_url) == _database_identity(test_url):
        raise RuntimeError(
            "PostgreSQL integration refuses the configured application database"
        )


def main() -> None:
    environment = load_backend_environment(PROJECT_ROOT)
    _require_separate_application_and_test_databases(environment)
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-m",
        "integration",
        "tests/db/test_postgres_integration.py",
        "tests/db/test_owned_assets_postgres.py",
        "tests/db/test_workflows_postgres.py",
        "tests/db/test_connectors_postgres.py",
        "tests/db/test_marketing_postgres.py",
        "tests/db/test_finance_postgres.py",
        "tests/db/test_learning_postgres.py",
    ]
    os.chdir(PROJECT_ROOT)
    os.execvpe(sys.executable, command, environment)


if __name__ == "__main__":
    main()
