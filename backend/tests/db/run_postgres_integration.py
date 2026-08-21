import os
import sys
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).parents[2]


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    environment = os.environ.copy()
    environment["DATABASE_URL"] = ""
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-m",
        "integration",
        "tests/db/test_postgres_integration.py",
        "tests/db/test_owned_assets_postgres.py",
    ]
    os.chdir(PROJECT_ROOT)
    os.execvpe(sys.executable, command, environment)


if __name__ == "__main__":
    main()
