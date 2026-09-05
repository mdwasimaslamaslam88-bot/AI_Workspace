from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_filesystem_tool_roots_default_to_deny_all():
    assert Settings(_env_file=None).FILESYSTEM_TOOL_ROOTS == ()


def test_filesystem_tool_root_accepts_only_explicit_narrow_absolute_path(tmp_path):
    root = tmp_path / "owner-workspaces"
    configured = Settings(_env_file=None, FILESYSTEM_TOOL_ROOTS=[root])

    assert configured.FILESYSTEM_TOOL_ROOTS == (root.resolve(),)


@pytest.mark.parametrize(
    "value",
    [
        ["relative/path"],
        ["/"],
        [str(Path.home().parent)],
        [str(Path.home())],
        ["/etc/ai-os"],
        [str(Path(__file__).resolve().parents[1] / "private-workspace")],
    ],
)
def test_filesystem_tool_root_rejects_broad_protected_or_source_paths(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, FILESYSTEM_TOOL_ROOTS=value)


def test_filesystem_tool_roots_reject_duplicates_and_unbounded_count(tmp_path):
    root = tmp_path / "root"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, FILESYSTEM_TOOL_ROOTS=[root, root])
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            FILESYSTEM_TOOL_ROOTS=[tmp_path / str(index) for index in range(5)],
        )
