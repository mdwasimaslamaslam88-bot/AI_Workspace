from __future__ import annotations

from typing import Protocol

from sqlalchemy.engine import make_url


class RuntimeDatabaseSettings(Protocol):
    DATABASE_URL: object | None
    TEST_DATABASE_URL: object | None


def _identity(value: object) -> tuple[str | None, int, str | None]:
    parsed = make_url(str(value))
    return (parsed.host, parsed.port or 5432, parsed.database)


def select_disposable_runtime_database(
    configured: RuntimeDatabaseSettings,
) -> None:
    application_url = configured.DATABASE_URL
    test_url = configured.TEST_DATABASE_URL
    if application_url is None or test_url is None:
        raise RuntimeError(
            "application and disposable test databases must both be configured"
        )
    if _identity(application_url) == _identity(test_url):
        raise RuntimeError(
            "runtime smoke refuses the configured application database"
        )
    test_identity = _identity(test_url)
    if test_identity[0] != "127.0.0.1" or test_identity[2] != "ai_workspace_test":
        raise RuntimeError(
            "runtime smoke requires the approved loopback disposable database"
        )
    configured.DATABASE_URL = test_url
