from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.v1.assets as assets_module
from app.api.dependencies import get_current_user
from app.api.v1.assets import router
from app.db.dependencies import get_db_session
from app.models import Asset, AssetProvenanceKind, User
from app.services.asset import AssetContent, AssetUploadResult
from app.storage.local import LocalAssetStorage


@pytest.fixture
def asset_api(tmp_path, monkeypatch):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.state.asset_storage = LocalAssetStorage(tmp_path / "assets")
    session = AsyncMock(spec=AsyncSession)
    user = User(id=uuid4())
    service = Mock()
    service.get_by_idempotency_key_for_owner = AsyncMock(return_value=None)
    service.upload_for_owner = AsyncMock()
    service.get_content_for_owner = AsyncMock()
    service.delete_for_owner = AsyncMock()
    service_factory = Mock(return_value=service)
    monkeypatch.setattr(assets_module, "AssetService", service_factory)

    async def database_override():
        yield session

    async def user_override():
        return user

    app.dependency_overrides[get_db_session] = database_override
    app.dependency_overrides[get_current_user] = user_override
    with TestClient(app) as client:
        yield {
            "app": app,
            "client": client,
            "session": session,
            "user": user,
            "service": service,
            "service_factory": service_factory,
        }


def _asset(owner_id, idempotency_key):
    asset_id = uuid4()
    return Asset(
        id=asset_id,
        owner_id=owner_id,
        original_filename='résumé "final".txt',
        media_type="text/plain",
        byte_size=7,
        content_sha256="a" * 64,
        storage_key=(
            f"objects/{asset_id.hex[:2]}/{asset_id.hex[2:4]}/{asset_id.hex}"
        ),
        upload_idempotency_key=idempotency_key,
        provenance_kind=AssetProvenanceKind.UPLOAD,
        created_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
        deleted_at=None,
    )


def test_upload_requires_uuid_idempotency_key(asset_api):
    response = asset_api["client"].post(
        "/api/v1/assets",
        files={"file": ("safe.txt", b"content", "text/plain")},
        headers={"Idempotency-Key": "not-a-uuid"},
    )

    assert response.status_code == 422
    asset_api["service"].upload_for_owner.assert_not_awaited()


def test_upload_returns_safe_metadata_and_never_storage_key(asset_api):
    key = uuid4()
    asset = _asset(asset_api["user"].id, key)
    asset_api["service"].upload_for_owner.return_value = AssetUploadResult(
        asset=asset,
        created=True,
    )

    response = asset_api["client"].post(
        "/api/v1/assets",
        files={"file": ("résumé.txt", b"content", "text/plain")},
        headers={"Idempotency-Key": str(key)},
    )

    assert response.status_code == 201
    assert response.json() == {
        "id": str(asset.id),
        "original_filename": 'résumé "final".txt',
        "media_type": "text/plain",
        "byte_size": 7,
        "content_sha256": "a" * 64,
        "provenance_kind": "upload",
        "source_asset_id": None,
        "runtime_id": None,
        "model_id": None,
        "created_at": "2026-08-21T00:00:00Z",
        "deleted_at": None,
    }
    assert "storage_key" not in response.text
    kwargs = asset_api["service"].upload_for_owner.await_args.kwargs
    assert kwargs["filename"] == "résumé.txt"
    assert kwargs["claimed_media_type"] == "text/plain"


def test_sequential_idempotency_replay_skips_multipart_consumption(asset_api):
    key = uuid4()
    asset = _asset(asset_api["user"].id, key)
    asset_api["service"].get_by_idempotency_key_for_owner.return_value = asset

    response = asset_api["client"].post(
        "/api/v1/assets",
        content=b"this is intentionally not multipart",
        headers={"Idempotency-Key": str(key)},
    )

    assert response.status_code == 200
    assert response.json()["id"] == str(asset.id)
    asset_api["service"].upload_for_owner.assert_not_awaited()


@pytest.mark.parametrize(
    "files,data",
    [
        ({}, {}),
        ({"wrong": ("safe.txt", b"content", "text/plain")}, {}),
        ({"file": ("safe.txt", b"content", "text/plain")}, {"extra": "value"}),
        (
            [
                ("file", ("one.txt", b"one", "text/plain")),
                ("file", ("two.txt", b"two", "text/plain")),
            ],
            {},
        ),
    ],
)
def test_upload_rejects_missing_duplicate_or_unexpected_parts(asset_api, files, data):
    response = asset_api["client"].post(
        "/api/v1/assets",
        files=files,
        data=data,
        headers={"Idempotency-Key": str(uuid4())},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Upload could not be accepted"}
    asset_api["service"].upload_for_owner.assert_not_awaited()


def test_authentication_fails_before_malformed_multipart_is_parsed(asset_api):
    async def reject_user():
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")

    asset_api["app"].dependency_overrides[get_current_user] = reject_user
    response = asset_api["client"].post(
        "/api/v1/assets",
        content=b"private malformed multipart bytes",
        headers={
            "Idempotency-Key": str(uuid4()),
            "Content-Type": "multipart/form-data; boundary=missing",
        },
    )

    assert response.status_code == 401
    assert "private malformed multipart bytes" not in response.text
    asset_api["service_factory"].assert_not_called()


def test_download_is_opaque_authenticated_no_store_and_filename_safe(asset_api):
    asset_id = uuid4()
    writer = asset_api["app"].state.asset_storage.begin_write(asset_id)
    writer.write(b"content")
    storage_key = writer.finalize()
    asset_api["service"].get_content_for_owner.return_value = AssetContent(
        storage_key=storage_key,
        original_filename='résumé "final".txt',
        media_type="text/plain",
        byte_size=7,
    )

    response = asset_api["client"].get(f"/api/v1/assets/{asset_id}/content")

    assert response.status_code == 200
    assert response.content == b"content"
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-asset-media-type"] == "text/plain"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["accept-ranges"] == "none"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "filename*=UTF-8''" in disposition
    assert "\r" not in disposition and "\n" not in disposition
    assert str(asset_api["app"].state.asset_storage.root) not in response.text


def test_range_is_rejected_without_opening_content(asset_api):
    asset_id = uuid4()
    asset_api["service"].get_content_for_owner.return_value = AssetContent(
        storage_key="objects/aa/aa/" + "a" * 32,
        original_filename=None,
        media_type="application/octet-stream",
        byte_size=7,
    )
    asset_api["app"].state.asset_storage.open_read = Mock()

    response = asset_api["client"].get(
        f"/api/v1/assets/{asset_id}/content",
        headers={"Range": "bytes=0-1"},
    )

    assert response.status_code == 416
    assert response.headers["content-range"] == "bytes */7"
    asset_api["app"].state.asset_storage.open_read.assert_not_called()


def test_unknown_unowned_and_deleted_downloads_share_404(asset_api):
    asset_api["service"].get_content_for_owner.return_value = None

    response = asset_api["client"].get(f"/api/v1/assets/{uuid4()}/content")

    assert response.status_code == 404
    assert response.json() == {"detail": "Asset not found"}


def test_active_metadata_with_missing_file_is_generic_503(asset_api):
    asset_api["service"].get_content_for_owner.return_value = AssetContent(
        storage_key="objects/aa/aa/" + "a" * 32,
        original_filename="private-name.txt",
        media_type="text/plain",
        byte_size=7,
    )

    response = asset_api["client"].get(f"/api/v1/assets/{uuid4()}/content")

    assert response.status_code == 503
    assert response.json() == {"detail": "Attachment content is unavailable"}
    assert "private-name" not in response.text
    assert "objects/" not in response.text


def test_delete_is_owner_scoped_and_already_deleted_is_idempotent(asset_api):
    asset_api["service"].delete_for_owner.return_value = True
    asset_id = uuid4()

    first = asset_api["client"].delete(f"/api/v1/assets/{asset_id}")
    second = asset_api["client"].delete(f"/api/v1/assets/{asset_id}")

    assert first.status_code == 204
    assert second.status_code == 204
    assert first.content == b""


def test_unconfigured_storage_keeps_text_mvp_up_but_assets_return_503(asset_api):
    asset_api["app"].state.asset_storage = None

    response = asset_api["client"].delete(f"/api/v1/assets/{uuid4()}")

    assert response.status_code == 503
    assert response.json() == {"detail": "Attachment storage is unavailable"}
    asset_api["service_factory"].assert_not_called()


def test_authenticated_malformed_multipart_is_generic_400(asset_api):
    response = asset_api["client"].post(
        "/api/v1/assets",
        content=b"private malformed multipart bytes",
        headers={
            "Idempotency-Key": str(uuid4()),
            "Content-Type": "multipart/form-data; boundary=missing",
        },
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Upload could not be accepted"}
    assert "private malformed" not in response.text
    asset_api["service"].upload_for_owner.assert_not_awaited()


def test_file_size_mismatch_is_generic_503(asset_api):
    asset_id = uuid4()
    writer = asset_api["app"].state.asset_storage.begin_write(asset_id)
    writer.write(b"short")
    storage_key = writer.finalize()
    asset_api["service"].get_content_for_owner.return_value = AssetContent(
        storage_key=storage_key,
        original_filename="private.txt",
        media_type="text/plain",
        byte_size=99,
    )

    response = asset_api["client"].get(f"/api/v1/assets/{asset_id}/content")

    assert response.status_code == 503
    assert response.json() == {"detail": "Attachment content is unavailable"}
    assert "private.txt" not in response.text
