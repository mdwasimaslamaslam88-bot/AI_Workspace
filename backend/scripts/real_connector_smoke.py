from __future__ import annotations

import asyncio
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import logging
from pathlib import Path
from threading import Thread

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.clients.postgres import create_postgres_engine, dispose_postgres
from app.connectors import ConnectorCredentialBox, ConnectorRuntime
from app.core.config import settings
from app.main import app
from scripts.runtime_smoke_safety import select_disposable_runtime_database


_CREDENTIAL = "local-connector-secret-123456789"
_PRIVATE_ACTION = "synchronize-owner-records"


class _ConnectedAppHandler(BaseHTTPRequestHandler):
    action_attempts = 0

    def _authorized(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {_CREDENTIAL}"

    def _reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            self._reply(401, {"error": "unauthorized"})
        elif self.path == "/api/health":
            self._reply(200, {"status": "healthy"})
        elif self.path == "/api/capabilities":
            self._reply(
                200,
                {"capabilities": ["records.read", "records.synchronize"]},
            )
        else:
            self._reply(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._reply(401, {"error": "unauthorized"})
            return
        if self.path != "/api/actions":
            self._reply(404, {"error": "not_found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        type(self).action_attempts += 1
        if type(self).action_attempts == 1:
            self._reply(503, {"retry": True})
            return
        self._reply(
            200,
            {
                "accepted": payload.get("action") == _PRIVATE_ACTION,
                "idempotency": self.headers.get("Idempotency-Key") is not None,
            },
        )

    def log_message(self, _format: str, *_args) -> None:
        return


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


def main() -> None:
    select_disposable_runtime_database(settings)
    asyncio.run(_clean_disposable_database())
    provisioning_token = "c" * 43
    settings.USER_PROVISIONING_TOKEN_DIGEST = hashlib.sha256(
        provisioning_token.encode("utf-8")
    ).hexdigest()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ConnectedAppHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    state_root = Path(settings.ASSET_STORAGE_ROOT).parent / "connector-smoke-state"
    runtime = ConnectorRuntime(
        ConnectorCredentialBox(state_root),
        (origin,),
        ConnectorRuntime.create_client(),
    )
    captured_logs = io.StringIO()
    handler = logging.StreamHandler(captured_logs)
    logging.getLogger().addHandler(handler)

    try:
        with TestClient(app) as client:
            original_runtime = app.state.connector_runtime
            app.state.connector_runtime = runtime
            owner = {"Authorization": f"Bearer {_provision(client, provisioning_token)}"}
            foreign = {"Authorization": f"Bearer {_provision(client, provisioning_token)}"}
            created_response = client.post(
                "/api/v1/connectors",
                headers=owner,
                json={
                    "name": "Real local connected app",
                    "provider": "Local verification provider",
                    "service": "Owner records",
                    "kind": "local_api",
                    "base_url": origin,
                    "auth_kind": "bearer",
                    "credential": _CREDENTIAL,
                    "scopes": ["read", "write"],
                    "capabilities": ["records.read"],
                    "path_prefixes": ["/api/"],
                    "health_path": "/api/health",
                    "discovery_path": "/api/capabilities",
                    "enabled": True,
                    "timeout_seconds": 2,
                    "max_retries": 1,
                    "rate_limit_requests_per_minute": 30,
                },
            )
            if created_response.status_code != 201 or _CREDENTIAL in created_response.text:
                raise RuntimeError("connector registration was not secure")
            connector = created_response.json()
            connector_id = connector["id"]
            if not connector["credential_configured"]:
                raise RuntimeError("connector credential was not configured")
            if client.get(
                f"/api/v1/connectors/{connector_id}", headers=foreign
            ).status_code != 404:
                raise RuntimeError("foreign owner could inspect a connector")

            health = client.post(
                f"/api/v1/connectors/{connector_id}/health", headers=owner
            )
            if (
                health.status_code != 200
                or health.json()["payload"] != {"status": "healthy"}
            ):
                raise RuntimeError("real connector health check failed")

            discovery = client.post(
                f"/api/v1/connectors/{connector_id}/discover", headers=owner
            )
            if (
                discovery.status_code != 200
                or discovery.json()["payload"]
                != {"capabilities": ["records.read", "records.synchronize"]}
            ):
                raise RuntimeError("real connector capability discovery failed")
            discovered_connector = client.get(
                f"/api/v1/connectors/{connector_id}", headers=owner
            ).json()
            if (
                discovered_connector["capabilities"]
                != ["records.read", "records.synchronize"]
                or discovered_connector["last_successful_test_at"] is None
                or discovered_connector["audit_reference"]
                != discovery.json()["execution"]["id"]
            ):
                raise RuntimeError("connector registry evidence was not persisted")

            disconnected = client.post(
                f"/api/v1/connectors/{connector_id}/disconnect", headers=owner
            )
            if (
                disconnected.status_code != 200
                or disconnected.json()["connection_status"] != "disabled"
                or not disconnected.json()["credential_configured"]
            ):
                raise RuntimeError("connector disconnect was not reversible")
            disconnected_action = client.post(
                f"/api/v1/connectors/{connector_id}/executions",
                headers=owner,
                json={"method": "GET", "path": "/api/health"},
            )
            if disconnected_action.status_code != 409:
                raise RuntimeError("disconnected connector remained executable")
            reconnect = client.post(
                f"/api/v1/connectors/{connector_id}/reconnect", headers=owner
            )
            if (
                reconnect.status_code != 200
                or reconnect.json()["payload"] != {"status": "healthy"}
            ):
                raise RuntimeError("connector reconnect was not health verified")

            action = client.post(
                f"/api/v1/connectors/{connector_id}/executions",
                headers=owner,
                json={
                    "method": "POST",
                    "path": "/api/actions",
                    "json_body": {"action": _PRIVATE_ACTION},
                    "idempotency_key": "real-action-000001",
                },
            )
            if (
                action.status_code != 201
                or action.json()["payload"]
                != {"accepted": True, "idempotency": True}
                or action.json()["execution"]["attempts"] != 2
            ):
                raise RuntimeError("real connector action or retry failed")

            denied = client.post(
                f"/api/v1/connectors/{connector_id}/executions",
                headers=owner,
                json={"method": "GET", "path": "/private/admin"},
            )
            if (
                denied.status_code != 403
                or "X-Connector-Execution-ID" not in denied.headers
            ):
                raise RuntimeError("connector path scope was not enforced")
            audits = client.get(
                f"/api/v1/connectors/executions?connector_id={connector_id}",
                headers=owner,
            )
            audits.raise_for_status()
            if [item["status"] for item in audits.json()["items"]] != [
                "failed",
                "completed",
                "completed",
                "failed",
                "completed",
                "completed",
            ]:
                raise RuntimeError("connector audit history is incomplete")
            if client.get(
                "/api/v1/connectors/executions", headers=foreign
            ).json()["items"]:
                raise RuntimeError("connector audit crossed an owner boundary")

            revoked = client.delete(
                f"/api/v1/connectors/{connector_id}", headers=owner
            )
            if (
                revoked.status_code != 200
                or revoked.json()["connection_status"] != "revoked"
                or revoked.json()["credential_configured"]
            ):
                raise RuntimeError("connector credential revocation failed")
            after_revoke = client.post(
                f"/api/v1/connectors/{connector_id}/executions",
                headers=owner,
                json={"method": "GET", "path": "/api/health"},
            )
            if after_revoke.status_code != 409:
                raise RuntimeError("revoked connector remained executable")
            app.state.connector_runtime = original_runtime
            client.portal.call(runtime.close)
    finally:
        logging.getLogger().removeHandler(handler)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    logs = captured_logs.getvalue()
    if _CREDENTIAL in logs or _PRIVATE_ACTION in logs:
        raise RuntimeError("connector secret or private action leaked into logs")
    print("REAL_CONNECTOR_LOCAL_API=passed")
    print("CONNECTOR_ENCRYPTED_AUTH=passed")
    print("CONNECTOR_SCOPE_OWNER_AUDIT_RETRY=passed")
    print("CONNECTOR_DISCOVER_DISCONNECT_RECONNECT=passed")
    print("CONNECTOR_REVOCATION=passed")


if __name__ == "__main__":
    main()
