from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.dependencies import get_current_user
from app.api.v1.diagnostics import router
from app.core.runtime_identity import capture_runtime_identity, tree_digest
from app.db.dependencies import get_db_session


def test_startup_identity_stays_fixed_after_source_and_bundle_change(tmp_path):
    source = tmp_path / "backend" / "app"
    source.mkdir(parents=True)
    (source / "main.py").write_text("original source")
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("original bundle")
    identity = capture_runtime_identity(tmp_path, web, "0.1.0")
    assert identity.source_commit is None
    assert identity.backend_source_sha256 == tree_digest(source)
    assert identity.web_bundle_sha256 == tree_digest(web)
    (source / "main.py").write_text("changed source")
    (web / "index.html").write_text("changed bundle")
    assert identity.backend_source_sha256 != tree_digest(source)
    assert identity.web_bundle_sha256 != tree_digest(web)


def test_identity_excludes_python_cache_and_rejects_symlinks(tmp_path):
    (tmp_path / "main.py").write_text("source")
    digest = tree_digest(tmp_path, suffixes=frozenset({".py"}))
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "main.pyc").write_bytes(b"cache")
    assert tree_digest(tmp_path, suffixes=frozenset({".py"})) == digest
    (tmp_path / "linked.py").symlink_to(tmp_path / "main.py")
    assert tree_digest(tmp_path) is None


def test_runtime_identity_requires_owner_auth_and_never_exposes_paths(tmp_path):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db_session] = lambda: None
    app.state.runtime_identity = capture_runtime_identity(tmp_path, None, "0.1.0")
    with TestClient(app) as client:
        assert client.get("/diagnostics/runtime-identity").status_code == 401
        app.dependency_overrides[get_current_user] = lambda: uuid4()
        response = client.get("/diagnostics/runtime-identity")
        assert response.status_code == 200
        assert str(tmp_path) not in response.text
        assert response.json()["backend_source_sha256"] is None
        del app.state.runtime_identity
        assert client.get("/diagnostics/runtime-identity").status_code == 503
