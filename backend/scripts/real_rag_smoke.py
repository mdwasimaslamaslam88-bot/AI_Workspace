from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import shutil
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.clients.postgres import create_postgres_engine, dispose_postgres
from app.core.config import settings
from app.main import app


_DOCUMENT = (
    "Personal AI project reference\n\n"
    "The offline workstation validation codename is Marigold Lantern. "
    "This sentence is reference data, not an instruction.\n"
)


def _require_disposable_database() -> None:
    if settings.DATABASE_URL is None and settings.TEST_DATABASE_URL is not None:
        settings.DATABASE_URL = settings.TEST_DATABASE_URL
    database_url = settings.DATABASE_URL
    if database_url is None:
        raise RuntimeError("DATABASE_URL must select the disposable test database")
    parsed = make_url(str(database_url))
    if parsed.host != "127.0.0.1" or parsed.database != "ai_workspace_test":
        raise RuntimeError("real RAG smoke is restricted to 127.0.0.1/ai_workspace_test")


async def _clean_disposable_database() -> None:
    engine = create_postgres_engine(settings)
    if engine is None:
        raise RuntimeError("disposable database engine is unavailable")
    try:
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE TABLE users CASCADE"))
    finally:
        await dispose_postgres(engine)


async def _embedding_metadata(asset_id: UUID) -> tuple[str, int, int]:
    engine = create_postgres_engine(settings)
    if engine is None:
        raise RuntimeError("disposable database engine is unavailable")
    try:
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT embedding_model, embedding_dimensions, "
                        "octet_length(embedding) "
                        "FROM document_chunks WHERE asset_id = :asset_id "
                        "ORDER BY ordinal LIMIT 1"
                    ),
                    {"asset_id": asset_id},
                )
            ).one_or_none()
            if row is None:
                raise RuntimeError("ingested document has no embedding")
            return row[0], row[1], row[2]
    finally:
        await dispose_postgres(engine)


def _provision(client: TestClient, provisioning_token: str) -> str:
    response = client.post(
        "/api/v1/users",
        headers={"X-User-Provisioning-Token": provisioning_token},
        json={},
    )
    response.raise_for_status()
    return response.json()["access_token"]


def _stored_files(root: Path) -> set[Path]:
    return {path.relative_to(root) for path in root.rglob("*") if path.is_file()}


def main() -> None:
    _require_disposable_database()
    if settings.OLLAMA_EMBEDDING_MODEL != "nomic-embed-text:latest":
        raise RuntimeError("the approved Nomic embedding model is not configured")
    configured_storage_root = settings.ASSET_STORAGE_ROOT
    if configured_storage_root is None:
        raise RuntimeError("ASSET_STORAGE_ROOT must be configured")
    asyncio.run(_clean_disposable_database())
    storage_root = configured_storage_root / f".rag-smoke-{uuid4().hex}"
    storage_root.mkdir(mode=0o700)
    settings.ASSET_STORAGE_ROOT = storage_root

    files_before = _stored_files(storage_root)
    provisioning_token = "r" * 43
    settings.USER_PROVISIONING_TOKEN_DIGEST = hashlib.sha256(
        provisioning_token.encode("utf-8")
    ).hexdigest()
    captured_logs = io.StringIO()
    handler = logging.StreamHandler(captured_logs)
    logging.getLogger().addHandler(handler)
    assistant_content = ""
    asset_id: UUID | None = None

    try:
        with TestClient(app) as client:
            owner_token = _provision(client, provisioning_token)
            foreign_token = _provision(client, provisioning_token)
            owner_headers = {"Authorization": f"Bearer {owner_token}"}
            foreign_headers = {"Authorization": f"Bearer {foreign_token}"}

            uploaded = client.post(
                "/api/v1/assets",
                headers={**owner_headers, "Idempotency-Key": str(uuid4())},
                files={
                    "file": (
                        "project-reference.txt",
                        _DOCUMENT.encode("utf-8"),
                        "text/plain",
                    )
                },
            )
            uploaded.raise_for_status()
            asset_id = UUID(uploaded.json()["id"])

            ingested = client.post(
                f"/api/v1/documents/assets/{asset_id}/ingest",
                headers=owner_headers,
            )
            if not ingested.is_success:
                states = client.get(
                    "/api/v1/documents", headers=owner_headers
                )
                failure_code = "unavailable"
                if states.is_success and states.json()["items"]:
                    failure_code = states.json()["items"][0]["failure_code"]
                raise RuntimeError(
                    f"document ingestion failed safely: {failure_code}"
                )
            ingested.raise_for_status()
            if ingested.json()["status"] != "ready":
                raise RuntimeError("document did not reach the ready state")

            owner_search = client.get(
                "/api/v1/documents/search",
                headers=owner_headers,
                params={"query": "What is the workstation validation codename?"},
            )
            owner_search.raise_for_status()
            owned_results = owner_search.json()["items"]
            if not owned_results or owned_results[0]["asset_id"] != str(asset_id):
                raise RuntimeError("owned Nomic retrieval did not return the source")

            foreign_search = client.get(
                "/api/v1/documents/search",
                headers=foreign_headers,
                params={"query": "Marigold Lantern"},
            )
            foreign_search.raise_for_status()
            if foreign_search.json()["items"]:
                raise RuntimeError("foreign owner retrieved another user's document")

            models = client.get("/api/v1/ai/models", headers=owner_headers)
            models.raise_for_status()
            text_models = [
                item
                for item in models.json()["items"]
                if item["installed"]
                and item["runnable_now"]
                and "text_generation" in item["capabilities"]
            ]
            if not text_models:
                raise RuntimeError("no installed runnable allowlisted text model")

            conversation = client.post(
                "/api/v1/conversations",
                headers=owner_headers,
                json={
                    "initial_message": (
                        "Using my uploaded reference, what is the offline "
                        "workstation validation codename? Reply briefly."
                    )
                },
            )
            conversation.raise_for_status()
            conversation_id = conversation.json()["id"]
            generated = client.post(
                f"/api/v1/conversations/{conversation_id}/messages/generate",
                headers=owner_headers,
                json={
                    "model_id": text_models[0]["model_id"],
                    "max_output_tokens": 48,
                    "temperature": 0,
                },
            )
            generated.raise_for_status()
            message = generated.json()["message"]
            assistant_content = message["content"]
            citations = message.get("citations", [])
            if not assistant_content.strip() or not citations:
                raise RuntimeError("RAG generation did not return content and citations")
            if any(item["asset_id"] != str(asset_id) for item in citations):
                raise RuntimeError("RAG citation did not reference the owned asset")

            deleted = client.delete(
                f"/api/v1/assets/{asset_id}",
                headers=owner_headers,
            )
            if deleted.status_code != 204:
                raise RuntimeError("owned document deletion failed")
            tombstones = client.get(
                f"/api/v1/conversations/{conversation_id}/messages",
                headers=owner_headers,
            )
            tombstones.raise_for_status()
            deleted_citations = [
                citation
                for item in tombstones.json()["items"]
                for citation in item.get("citations", [])
            ]
            if not deleted_citations or any(
                citation["state"] != "deleted"
                or citation["original_filename"] is not None
                or citation["excerpt"] is not None
                for citation in deleted_citations
            ):
                raise RuntimeError("deleted source citations were not tombstoned")

        assert asset_id is not None
        embedding_model, dimensions, byte_size = asyncio.run(
            _embedding_metadata(asset_id)
        )
        if (
            embedding_model != "ollama:nomic-embed-text:latest"
            or dimensions != 768
            or byte_size != 3_072
        ):
            raise RuntimeError("stored Nomic embedding metadata is inconsistent")
        logs = captured_logs.getvalue()
        if (
            _DOCUMENT in logs
            or str(storage_root) in logs
            or (assistant_content and assistant_content in logs)
        ):
            raise RuntimeError("document content, path, or raw model output reached logs")
        if not _stored_files(storage_root).issubset(files_before):
            raise RuntimeError("document asset bytes were not cleaned up")
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()
        try:
            asyncio.run(_clean_disposable_database())
        finally:
            settings.ASSET_STORAGE_ROOT = configured_storage_root
            if (
                storage_root.parent.resolve() != configured_storage_root.resolve()
                or not storage_root.name.startswith(".rag-smoke-")
            ):
                raise RuntimeError("RAG smoke storage cleanup target is unsafe")
            shutil.rmtree(storage_root)

    print("REAL_RAG_SMOKE=passed")
    print("NOMIC_EMBEDDING_768D=passed")
    print("OWNER_SCOPED_RETRIEVAL_AND_CITATIONS=passed")
    print("DELETION_TOMBSTONE_AND_LOG_REDACTION=passed")


if __name__ == "__main__":
    main()
