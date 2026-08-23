from dataclasses import dataclass

import pytest

from scripts.runtime_smoke_safety import select_disposable_runtime_database


@dataclass
class DatabaseSettings:
    DATABASE_URL: object | None
    TEST_DATABASE_URL: object | None


def test_runtime_smoke_selects_only_a_distinct_approved_database() -> None:
    settings = DatabaseSettings(
        DATABASE_URL="postgresql+asyncpg://app@127.0.0.1/ai_workspace",
        TEST_DATABASE_URL=(
            "postgresql+asyncpg://tester@127.0.0.1/ai_workspace_test"
        ),
    )

    select_disposable_runtime_database(settings)

    assert settings.DATABASE_URL == settings.TEST_DATABASE_URL


@pytest.mark.parametrize(
    ("application_url", "test_url"),
    [
        (None, "postgresql+asyncpg://tester@127.0.0.1/ai_workspace_test"),
        ("postgresql+asyncpg://app@127.0.0.1/ai_workspace", None),
        (
            "postgresql+asyncpg://app@127.0.0.1/ai_workspace_test",
            "postgresql+asyncpg://tester@127.0.0.1/ai_workspace_test",
        ),
        (
            "postgresql+asyncpg://app@127.0.0.1/ai_workspace",
            "postgresql+asyncpg://tester@localhost/ai_workspace_test",
        ),
        (
            "postgresql+asyncpg://app@127.0.0.1/ai_workspace",
            "postgresql+asyncpg://tester@127.0.0.1/not_disposable",
        ),
    ],
)
def test_runtime_smoke_rejects_missing_matching_or_unapproved_database(
    application_url: str | None,
    test_url: str | None,
) -> None:
    settings = DatabaseSettings(application_url, test_url)

    with pytest.raises(RuntimeError) as raised:
        select_disposable_runtime_database(settings)

    assert "postgresql" not in str(raised.value)
    assert settings.DATABASE_URL == application_url
