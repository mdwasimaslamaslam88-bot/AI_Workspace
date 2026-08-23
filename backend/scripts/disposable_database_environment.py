from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from dotenv import load_dotenv


EPHEMERAL_TEST_DATABASE_URL = "WORK_STATION_EPHEMERAL_TEST_DATABASE_URL"


def apply_ephemeral_test_database_override(
    source: Mapping[str, str],
) -> dict[str, str]:
    """Return a child environment with only the disposable test URL replaced."""

    environment = dict(source)
    override = environment.pop(EPHEMERAL_TEST_DATABASE_URL, "").strip()
    if override:
        environment["TEST_DATABASE_URL"] = override
    return environment


def load_backend_environment(project_root: Path) -> dict[str, str]:
    """Load protected app configuration before applying a test-only override."""

    load_dotenv(project_root / ".env", override=False)
    return apply_ephemeral_test_database_override(os.environ)
