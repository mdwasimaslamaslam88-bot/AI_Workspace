from __future__ import annotations

import asyncio
import hashlib
import io
import logging
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.clients.postgres import create_postgres_engine, dispose_postgres
from app.core.config import settings
from app.main import app


_MEMORIES = (
    ("preference", "My preferred codeword color is cerulean."),
    ("fact", "The local workstation is named Hearthstone."),
    ("instruction", "Prefer concise answers unless I currently request detail."),
    ("project_context", "The Atlas project uses local-only inference."),
)


def _require_disposable_database() -> None:
    if settings.DATABASE_URL is None and settings.TEST_DATABASE_URL is not None:
        settings.DATABASE_URL = settings.TEST_DATABASE_URL
    database_url = settings.DATABASE_URL
    if database_url is None:
        raise RuntimeError("DATABASE_URL must select the disposable test database")
    parsed = make_url(str(database_url))
    if parsed.host != "127.0.0.1" or parsed.database != "ai_workspace_test":
        raise RuntimeError(
            "real memory smoke is restricted to 127.0.0.1/ai_workspace_test"
        )


async def _clean_disposable_database() -> None:
    engine = create_postgres_engine(settings)
    if engine is None:
        raise RuntimeError("disposable database engine is unavailable")
    try:
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE TABLE users CASCADE"))
    finally:
        await dispose_postgres(engine)


async def _forgotten_state(memory_id: UUID) -> tuple[object, ...]:
    engine = create_postgres_engine(settings)
    if engine is None:
        raise RuntimeError("disposable database engine is unavailable")
    try:
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT content, embedding, embedding_norm, deleted_at "
                        "FROM memories WHERE id = :memory_id"
                    ),
                    {"memory_id": memory_id},
                )
            ).one_or_none()
            if row is None:
                raise RuntimeError("forgotten memory tombstone is missing")
            return tuple(row)
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


def main() -> None:
    _require_disposable_database()
    if settings.OLLAMA_BASE_URL is None:
        raise RuntimeError("OLLAMA_BASE_URL must be configured")
    asyncio.run(_clean_disposable_database())
    provisioning_token = "m" * 43
    settings.USER_PROVISIONING_TOKEN_DIGEST = hashlib.sha256(
        provisioning_token.encode("utf-8")
    ).hexdigest()
    captured_logs = io.StringIO()
    handler = logging.StreamHandler(captured_logs)
    logging.getLogger().addHandler(handler)
    assistant_content = ""
    preferred_memory_id: UUID | None = None

    try:
        with TestClient(app) as client:
            owner_token = _provision(client, provisioning_token)
            foreign_token = _provision(client, provisioning_token)
            owner_headers = {"Authorization": f"Bearer {owner_token}"}
            foreign_headers = {"Authorization": f"Bearer {foreign_token}"}

            default_setting = client.get(
                "/api/v1/memories/settings", headers=owner_headers
            )
            default_setting.raise_for_status()
            if default_setting.json()["enabled"] is not True:
                raise RuntimeError("memory was not enabled by its safe default")

            created = []
            for category, content in _MEMORIES:
                response = client.post(
                    "/api/v1/memories",
                    headers=owner_headers,
                    json={"category": category, "content": content},
                )
                response.raise_for_status()
                body = response.json()
                if (
                    body["state"] != "active"
                    or body["provenance_kind"] != "explicit_user_entry"
                    or "owner_id" in body
                    or "embedding" in body
                ):
                    raise RuntimeError("memory response exposed unsafe state")
                created.append(body)
            preferred_memory_id = UUID(created[0]["id"])

            owner_list = client.get("/api/v1/memories", headers=owner_headers)
            owner_list.raise_for_status()
            if len(owner_list.json()["items"]) != len(_MEMORIES):
                raise RuntimeError("owner could not inspect explicit memories")
            foreign_list = client.get(
                "/api/v1/memories", headers=foreign_headers
            )
            foreign_list.raise_for_status()
            if foreign_list.json()["items"]:
                raise RuntimeError("foreign owner could inspect memories")
            if client.delete(
                f"/api/v1/memories/{preferred_memory_id}",
                headers=foreign_headers,
            ).status_code != 404:
                raise RuntimeError("foreign owner could forget a memory")

            search = client.get(
                "/api/v1/memories/search",
                headers=owner_headers,
                params={"query": "What is my preferred codeword color?"},
            )
            search.raise_for_status()
            if not any(
                item["id"] == str(preferred_memory_id)
                and "cerulean" in item["content"].lower()
                for item in search.json()["items"]
            ):
                raise RuntimeError("owned memory retrieval did not return the preference")

            disabled = client.put(
                "/api/v1/memories/settings",
                headers=owner_headers,
                json={"enabled": False},
            )
            disabled.raise_for_status()
            disabled_search = client.get(
                "/api/v1/memories/search",
                headers=owner_headers,
                params={"query": "cerulean"},
            )
            disabled_search.raise_for_status()
            if disabled_search.json()["items"]:
                raise RuntimeError("disabled memory still materialized retrieval")
            enabled = client.put(
                "/api/v1/memories/settings",
                headers=owner_headers,
                json={"enabled": True},
            )
            enabled.raise_for_status()

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
                        "What is my preferred codeword color? Reply with the "
                        "color only; this current instruction has priority."
                    )
                },
            )
            conversation.raise_for_status()
            generated = client.post(
                f"/api/v1/conversations/{conversation.json()['id']}/messages/generate",
                headers=owner_headers,
                json={
                    "model_id": text_models[0]["model_id"],
                    "max_output_tokens": 32,
                    "temperature": 0,
                },
            )
            generated.raise_for_status()
            assistant_content = generated.json()["message"]["content"]
            if "cerulean" not in assistant_content.lower():
                raise RuntimeError("real generation did not use the owned memory")

            after_generation = client.get(
                "/api/v1/memories", headers=owner_headers
            )
            after_generation.raise_for_status()
            if len(after_generation.json()["items"]) != len(_MEMORIES):
                raise RuntimeError("chat was persisted as arbitrary memory")

            forgotten = client.delete(
                f"/api/v1/memories/{preferred_memory_id}",
                headers=owner_headers,
            )
            forgotten.raise_for_status()
            if (
                forgotten.json()["state"] != "deleted"
                or forgotten.json()["content"] is not None
                or forgotten.json()["deleted_at"] is None
            ):
                raise RuntimeError("forgotten memory was not content-free")
            post_forget_search = client.get(
                "/api/v1/memories/search",
                headers=owner_headers,
                params={"query": "cerulean"},
            )
            post_forget_search.raise_for_status()
            if any(
                item["id"] == str(preferred_memory_id)
                for item in post_forget_search.json()["items"]
            ):
                raise RuntimeError("forgotten memory remained retrievable")

        assert preferred_memory_id is not None
        content, embedding, norm, deleted_at = asyncio.run(
            _forgotten_state(preferred_memory_id)
        )
        if any(value is not None for value in (content, embedding, norm)):
            raise RuntimeError("forgotten memory retained private content")
        if deleted_at is None:
            raise RuntimeError("forgotten memory lacks its deletion timestamp")
        logs = captured_logs.getvalue()
        if any(content in logs for _category, content in _MEMORIES) or (
            assistant_content and assistant_content in logs
        ):
            raise RuntimeError("memory content or raw model output reached logs")
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()
        asyncio.run(_clean_disposable_database())

    print("REAL_MEMORY_SMOKE=passed")
    print("OWNER_ISOLATION_AND_EXPLICIT_PROVENANCE=passed")
    print("ENABLE_DISABLE_AND_GENERATION_RETRIEVAL=passed")
    print("CONTENT_FREE_FORGET_AND_LOG_REDACTION=passed")


if __name__ == "__main__":
    main()
