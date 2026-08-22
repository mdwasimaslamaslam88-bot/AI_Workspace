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


_MEMORY = "My private safety phrase is Copper Meadow."
_MESSAGE = "The conversation checkpoint is Silver Compass."
_DOCUMENT = (
    "Owned tool reference\n\n"
    "The document search checkpoint is Amber Lighthouse.\n"
)
_EXPECTED_TOOLS = {
    "calculator": "utility",
    "local_time": "utility",
    "document_search": "personal_documents_read",
    "conversation_search": "personal_conversations_read",
    "memory_search": "personal_memory_read",
}


def _require_disposable_database() -> None:
    if settings.DATABASE_URL is None and settings.TEST_DATABASE_URL is not None:
        settings.DATABASE_URL = settings.TEST_DATABASE_URL
    database_url = settings.DATABASE_URL
    if database_url is None:
        raise RuntimeError("DATABASE_URL must select the disposable test database")
    parsed = make_url(str(database_url))
    if parsed.host != "127.0.0.1" or parsed.database != "ai_workspace_test":
        raise RuntimeError("real tools smoke is restricted to the disposable test DB")


async def _clean_disposable_database() -> None:
    engine = create_postgres_engine(settings)
    if engine is None:
        raise RuntimeError("disposable database engine is unavailable")
    try:
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE TABLE users CASCADE"))
    finally:
        await dispose_postgres(engine)


def _provision(client: TestClient, token: str) -> str:
    response = client.post(
        "/api/v1/users",
        headers={"X-User-Provisioning-Token": token},
        json={},
    )
    response.raise_for_status()
    return response.json()["access_token"]


def _execute(
    client: TestClient,
    headers: dict[str, str],
    name: str,
    arguments: dict[str, object],
    *,
    conversation_id: str | None = None,
) -> dict[str, object]:
    body: dict[str, object] = {"arguments": arguments}
    if conversation_id is not None:
        body["conversation_id"] = conversation_id
    response = client.post(
        f"/api/v1/tools/{name}/executions",
        headers=headers,
        json=body,
    )
    response.raise_for_status()
    return response.json()


def _assert_completed(execution: dict[str, object], name: str) -> object:
    if (
        execution["tool_name"] != name
        or execution["status"] != "completed"
        or execution["initiator"] != "explicit_user"
        or execution["error_code"] is not None
        or execution["completed_at"] is None
        or not isinstance(execution["duration_ms"], int)
    ):
        raise RuntimeError(f"{name} did not produce a complete audit record")
    return execution["result"]


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
    storage_root = configured_storage_root / f".tools-smoke-{uuid4().hex}"
    storage_root.mkdir(mode=0o700)
    settings.ASSET_STORAGE_ROOT = storage_root
    files_before = _stored_files(storage_root)

    provisioning_token = "t" * 43
    settings.USER_PROVISIONING_TOKEN_DIGEST = hashlib.sha256(
        provisioning_token.encode("utf-8")
    ).hexdigest()
    captured_logs = io.StringIO()
    handler = logging.StreamHandler(captured_logs)
    logging.getLogger().addHandler(handler)
    asset_id: UUID | None = None

    try:
        with TestClient(app) as client:
            owner_headers = {
                "Authorization": f"Bearer {_provision(client, provisioning_token)}"
            }
            foreign_headers = {
                "Authorization": f"Bearer {_provision(client, provisioning_token)}"
            }

            registry = client.get("/api/v1/tools", headers=owner_headers)
            registry.raise_for_status()
            descriptors = registry.json()["items"]
            observed = {item["name"]: item["permission"] for item in descriptors}
            if observed != _EXPECTED_TOOLS:
                raise RuntimeError("the public tool registry is not the fixed registry")
            if any(
                not item["input_schema"]
                or item["timeout_seconds"] <= 0
                or item["max_output_characters"] <= 0
                for item in descriptors
            ):
                raise RuntimeError("a tool descriptor omitted a required bound")

            memory = client.post(
                "/api/v1/memories",
                headers=owner_headers,
                json={"category": "fact", "content": _MEMORY},
            )
            memory.raise_for_status()
            conversation = client.post(
                "/api/v1/conversations",
                headers=owner_headers,
                json={"initial_message": _MESSAGE},
            )
            conversation.raise_for_status()
            conversation_id = conversation.json()["id"]

            uploaded = client.post(
                "/api/v1/assets",
                headers={**owner_headers, "Idempotency-Key": str(uuid4())},
                files={
                    "file": (
                        "tool-reference.txt",
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
            ingested.raise_for_status()
            if ingested.json()["status"] != "ready":
                raise RuntimeError("the tool reference document was not ready")

            calculator = _assert_completed(
                _execute(client, owner_headers, "calculator", {"expression": "17*3+8"}),
                "calculator",
            )
            if calculator != {"value": 59}:
                raise RuntimeError("calculator returned an incorrect bounded result")

            local_time = _assert_completed(
                _execute(
                    client,
                    owner_headers,
                    "local_time",
                    {"timezone": "Asia/Kolkata"},
                ),
                "local_time",
            )
            if not isinstance(local_time, dict) or local_time.get("utc_offset") != "+0530":
                raise RuntimeError("local time returned an incorrect timezone offset")

            memory_result = _assert_completed(
                _execute(
                    client,
                    owner_headers,
                    "memory_search",
                    {"query": "What is my private safety phrase?"},
                ),
                "memory_search",
            )
            if not any(
                "Copper Meadow" in item["content"]
                for item in memory_result["items"]
            ):
                raise RuntimeError("owned memory search omitted its expected result")

            conversation_result = _assert_completed(
                _execute(
                    client,
                    owner_headers,
                    "conversation_search",
                    {"query": "Silver Compass", "conversation_id": conversation_id},
                    conversation_id=conversation_id,
                ),
                "conversation_search",
            )
            if not any(
                "Silver Compass" in item["excerpt"]
                for item in conversation_result["items"]
            ):
                raise RuntimeError("owned conversation search omitted its expected result")

            document_result = _assert_completed(
                _execute(
                    client,
                    owner_headers,
                    "document_search",
                    {"query": "What is the document search checkpoint?"},
                ),
                "document_search",
            )
            if not any(
                item["asset_id"] == str(asset_id)
                and "Amber Lighthouse" in item["content"]
                for item in document_result["items"]
            ):
                raise RuntimeError("real Nomic document tool search missed its source")

            for name, query in (
                ("memory_search", "Copper Meadow"),
                ("conversation_search", "Silver Compass"),
                ("document_search", "Amber Lighthouse"),
            ):
                result = _assert_completed(
                    _execute(client, foreign_headers, name, {"query": query}), name
                )
                if result != {"items": []}:
                    raise RuntimeError(f"{name} crossed its owner boundary")

            foreign_conversation = client.post(
                "/api/v1/tools/conversation_search/executions",
                headers=foreign_headers,
                json={
                    "arguments": {
                        "query": "Silver Compass",
                        "conversation_id": conversation_id,
                    },
                    "conversation_id": conversation_id,
                },
            )
            if foreign_conversation.status_code != 404:
                raise RuntimeError("foreign conversation binding was not hidden")

            dangerous = _execute(
                client,
                owner_headers,
                "calculator",
                {"expression": "__import__('os').system('id')"},
            )
            if (
                dangerous["status"] != "failed"
                or dangerous["result"] is not None
                or dangerous["error_code"] != "tool_execution_failed"
            ):
                raise RuntimeError("calculator did not safely reject code execution")
            unregistered = client.post(
                "/api/v1/tools/shell/executions",
                headers=owner_headers,
                json={"arguments": {"command": "id"}},
            )
            if unregistered.status_code != 404:
                raise RuntimeError("an unregistered tool was not rejected")

            owner_history = client.get(
                "/api/v1/tools/executions", headers=owner_headers
            )
            foreign_history = client.get(
                "/api/v1/tools/executions", headers=foreign_headers
            )
            owner_history.raise_for_status()
            foreign_history.raise_for_status()
            owner_items = owner_history.json()["items"]
            foreign_items = foreign_history.json()["items"]
            if len(owner_items) != 6 or len(foreign_items) != 3:
                raise RuntimeError("tool execution history is incomplete")
            if {item["id"] for item in owner_items} & {
                item["id"] for item in foreign_items
            }:
                raise RuntimeError("tool execution history crossed an owner boundary")

            deleted = client.delete(
                f"/api/v1/assets/{asset_id}", headers=owner_headers
            )
            if deleted.status_code != 204:
                raise RuntimeError("tool reference asset cleanup failed")

        logs = captured_logs.getvalue()
        if any(secret in logs for secret in (_MEMORY, _MESSAGE, _DOCUMENT)) or str(
            storage_root
        ) in logs:
            raise RuntimeError("private tool data or a storage path reached logs")
        if not _stored_files(storage_root).issubset(files_before):
            raise RuntimeError("tool reference bytes were not cleaned up")
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()
        try:
            asyncio.run(_clean_disposable_database())
        finally:
            settings.ASSET_STORAGE_ROOT = configured_storage_root
            if (
                storage_root.parent.resolve() != configured_storage_root.resolve()
                or not storage_root.name.startswith(".tools-smoke-")
            ):
                raise RuntimeError("tools smoke storage cleanup target is unsafe")
            shutil.rmtree(storage_root)

    print("REAL_TOOLS_SMOKE=passed")
    print("FIXED_REGISTRY_SCHEMA_PERMISSION_AND_BOUNDS=passed")
    print("OWNER_SCOPED_PERSONAL_SEARCH_AND_AUDIT=passed")
    print("NO_SHELL_CODE_OR_UNRESTRICTED_NETWORK=passed")


if __name__ == "__main__":
    main()
