from __future__ import annotations

import asyncio
import hashlib
import io
import logging
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.clients.postgres import create_postgres_engine, dispose_postgres
from app.core.config import settings
from app.main import app
from scripts.runtime_smoke_safety import select_disposable_runtime_database


_PROMPT = "Create AI_OS_REAL_TEST.txt with:\nAI OS REAL EXECUTION VERIFIED"
_CONTENT = "AI OS REAL EXECUTION VERIFIED"


async def _clean_disposable_database() -> None:
    engine = create_postgres_engine(settings)
    if engine is None:
        raise RuntimeError("disposable database engine is unavailable")
    try:
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE TABLE users CASCADE"))
    finally:
        await dispose_postgres(engine)


def main() -> None:
    select_disposable_runtime_database(settings)
    asyncio.run(_clean_disposable_database())
    if len(settings.FILESYSTEM_TOOL_ROOTS) != 1:
        raise RuntimeError("one disposable filesystem tool root is required")
    filesystem_root = Path(settings.FILESYSTEM_TOOL_ROOTS[0]).resolve(strict=True)
    provisioning_token = "c" * 43
    settings.USER_PROVISIONING_TOKEN_DIGEST = hashlib.sha256(
        provisioning_token.encode("utf-8")
    ).hexdigest()
    captured_logs = io.StringIO()
    handler = logging.StreamHandler(captured_logs)
    logging.getLogger().addHandler(handler)
    owner_workspace: Path | None = None

    try:
        with TestClient(app) as client:
            provisioned = client.post(
                "/api/v1/users",
                headers={"X-User-Provisioning-Token": provisioning_token},
                json={},
            )
            provisioned.raise_for_status()
            owner_id = UUID(provisioned.json()["id"])
            owner_workspace = filesystem_root / str(owner_id)
            headers = {
                "Authorization": f"Bearer {provisioned.json()['access_token']}"
            }
            foreign = client.post(
                "/api/v1/users",
                headers={"X-User-Provisioning-Token": provisioning_token},
                json={},
            )
            foreign.raise_for_status()
            foreign_headers = {
                "Authorization": f"Bearer {foreign.json()['access_token']}"
            }
            conversation = client.post(
                "/api/v1/conversations",
                headers=headers,
                json={"title": "Real chat tool smoke", "initial_message": _PROMPT},
            )
            conversation.raise_for_status()
            generation = client.post(
                f"/api/v1/conversations/{conversation.json()['id']}/messages/generate",
                headers=headers,
                json={"task": "tool_calling", "temperature": 0, "seed": 0},
            )
            generation.raise_for_status()
            payload = generation.json()
            execution = payload.get("execution")
            if (
                not isinstance(execution, dict)
                or execution.get("status") != "completed"
                or execution.get("states", [])[-1:] != ["done"]
            ):
                raise RuntimeError(
                    "real chat did not complete its structured tool loop: "
                    f"model={payload.get('model_id')!r}, "
                    f"status={execution.get('status') if isinstance(execution, dict) else None!r}, "
                    f"detail={execution.get('detail') if isinstance(execution, dict) else None!r}"
                )
            receipts = execution.get("receipts")
            if not isinstance(receipts, list) or [
                item.get("tool") for item in receipts
            ] != ["filesystem.write", "filesystem.exists", "filesystem.read"]:
                raise RuntimeError("real chat omitted independent filesystem evidence")
            if receipts[0].get("verification") != "exact_read_back_passed":
                raise RuntimeError("real chat write was not independently verified")
            if "AI OS verified execution: completed" not in payload["message"]["content"]:
                raise RuntimeError("chat response omitted its real execution receipt")

            created = owner_workspace / "AI_OS_REAL_TEST.txt"
            if not created.is_file() or created.is_symlink():
                raise RuntimeError("real chat did not create the expected regular file")
            if created.read_text(encoding="utf-8") != _CONTENT:
                raise RuntimeError("real chat file failed exact read-back")

            history = client.get("/api/v1/tools/executions", headers=headers)
            history.raise_for_status()
            filesystem_audits = [
                item
                for item in history.json()["items"]
                if item["tool_name"].startswith("filesystem.")
            ]
            if len(filesystem_audits) != 3:
                raise RuntimeError("durable filesystem audit count was not exact")
            if {item["initiator"] for item in filesystem_audits} != {
                "chat_model",
                "chat_verifier",
            }:
                raise RuntimeError("chat/verifier audit initiators were not preserved")
            if _CONTENT in str(filesystem_audits):
                raise RuntimeError("filesystem content leaked into audit history")

            foreign_exists = client.post(
                "/api/v1/tools/filesystem.exists/executions",
                headers=foreign_headers,
                json={"arguments": {"path": "AI_OS_REAL_TEST.txt"}},
            )
            foreign_exists.raise_for_status()
            if foreign_exists.json()["result"].get("exists") is not False:
                raise RuntimeError("filesystem state crossed its owner boundary")
            foreign_history = client.get(
                "/api/v1/tools/executions", headers=foreign_headers
            )
            foreign_history.raise_for_status()
            if {item["id"] for item in history.json()["items"]} & {
                item["id"] for item in foreign_history.json()["items"]
            }:
                raise RuntimeError("filesystem audit history crossed owners")

            created.unlink()
            owner_workspace.rmdir()
            owner_workspace = None
            foreign_workspace = filesystem_root / foreign.json()["id"]
            foreign_workspace.rmdir()

        logs = captured_logs.getvalue()
        if _CONTENT in logs or str(filesystem_root) in logs:
            raise RuntimeError("filesystem content or private root reached logs")
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()
        if owner_workspace is not None and owner_workspace.exists():
            created = owner_workspace / "AI_OS_REAL_TEST.txt"
            if created.is_file() and not created.is_symlink():
                created.unlink()
            owner_workspace.rmdir()

    print(f"CHAT_TOOL_MODEL_ID={payload['model_id']}")
    print(
        "real chat tool smoke: authenticated router, native model tool call, "
        "owner permission, write, exists/read verification, audit, and cleanup passed"
    )


if __name__ == "__main__":
    main()
