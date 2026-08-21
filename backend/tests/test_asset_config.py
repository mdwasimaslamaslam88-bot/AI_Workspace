from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_asset_storage_is_optional_for_the_existing_text_mvp(monkeypatch):
    monkeypatch.delenv("ASSET_STORAGE_ROOT", raising=False)

    assert Settings(_env_file=None).ASSET_STORAGE_ROOT is None


def test_asset_storage_accepts_an_absolute_root_outside_source(tmp_path):
    root = (tmp_path / "private-assets").resolve()

    assert Settings(_env_file=None, ASSET_STORAGE_ROOT=root).ASSET_STORAGE_ROOT == root


@pytest.mark.parametrize("value", ["relative/assets", Path("relative/assets")])
def test_asset_storage_rejects_relative_paths(value):
    with pytest.raises(ValidationError, match="absolute path"):
        Settings(_env_file=None, ASSET_STORAGE_ROOT=value)


def test_asset_storage_rejects_project_source_tree():
    project_root = Path(__file__).resolve().parents[2]

    with pytest.raises(ValidationError, match="outside the project source tree"):
        Settings(_env_file=None, ASSET_STORAGE_ROOT=project_root / "private-assets")
