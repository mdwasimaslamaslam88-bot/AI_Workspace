from __future__ import annotations

import asyncio
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import logging
from pathlib import Path
from threading import Thread

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.clients.postgres import create_postgres_engine, dispose_postgres
from app.connectors import ConnectorCredentialBox, ConnectorRuntime
from app.core.config import settings
from app.main import app
from scripts.runtime_smoke_safety import select_disposable_runtime_database


_LOCAL_CREDENTIAL = "local-business-provider-token-000000"
_CAPABILITIES = [
    "campaign.analytics.read",
    "campaign.publish",
    "cms.content.read",
    "cms.content.write",
    "cms.identity.read",
    "crm.contacts.read",
    "crm.contacts.write",
    "crm.deals.write",
    "crm.identity.read",
    "crm.notes.write",
    "crm.tasks.write",
    "social.analytics.read",
    "social.content.write",
    "social.draft.write",
    "social.identity.read",
]


class _BusinessProviderHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, object]] = []

    def _json(self, status: int, payload: dict[str, object]) -> None:
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _authorized(self) -> bool:
        if self.headers.get("Authorization") == f"Bearer {_LOCAL_CREDENTIAL}":
            return True
        self._json(401, {"error": "unauthorized"})
        return False

    def _body(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length)) if length else {}
        if not isinstance(payload, dict):
            raise ValueError("request body must be an object")
        return payload

    def _record(self, payload: dict[str, object]) -> None:
        type(self).requests.append(
            {
                "method": self.command,
                "path": self.path,
                "idempotency_key": self.headers.get("Idempotency-Key"),
                "payload": payload,
            }
        )

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            return
        responses: dict[str, dict[str, object]] = {
            "/api/health": {"status": "healthy"},
            "/api/capabilities": {"capabilities": _CAPABILITIES},
            "/api/crm/account": {"account_reference": "crm-test-account", "state": "verified"},
            "/api/crm/contacts/contact-1": {"provider_reference": "contact-1", "state": "present"},
            "/api/social/account": {"account_reference": "social-test-page", "state": "verified"},
            "/api/social/posts/social-1": {"provider_reference": "social-1", "state": "draft"},
            "/api/cms/site": {"site_reference": "cms-test-site", "state": "verified"},
            "/api/cms/posts/cms-1": {"provider_reference": "cms-1", "state": "draft"},
            "/api/marketing/analytics": {
                "observed_at": "2026-09-04T00:00:00Z",
                "provider_reference": "analytics-export-1",
                "state": "available",
            },
        }
        payload = responses.get(self.path)
        if payload is None:
            self._json(404, {"error": "not_found"})
            return
        self._record({})
        self._json(200, payload)

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            return
        payload = self._body()
        responses: dict[str, tuple[str, str]] = {
            "/api/crm/contacts": ("contact-1", "created"),
            "/api/crm/notes": ("note-1", "created"),
            "/api/crm/tasks": ("task-1", "created"),
            "/api/crm/deals": ("deal-1", "created"),
            "/api/social/drafts": ("social-1", "draft"),
            "/api/cms/posts": ("cms-1", "draft"),
        }
        result = responses.get(self.path)
        if result is None:
            self._json(404, {"error": "not_found"})
            return
        self._record(payload)
        self._json(201, {"provider_reference": result[0], "state": result[1]})

    def do_PATCH(self) -> None:  # noqa: N802
        if not self._authorized():
            return
        payload = self._body()
        references = {
            "/api/crm/contacts/contact-1": "contact-1",
            "/api/social/posts/social-1": "social-1",
            "/api/cms/posts/cms-1": "cms-1",
        }
        reference = references.get(self.path)
        if reference is None:
            self._json(404, {"error": "not_found"})
            return
        self._record(payload)
        self._json(200, {"provider_reference": reference, "state": "updated"})

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._authorized():
            return
        references = {
            "/api/social/posts/social-1": "social-1",
            "/api/cms/posts/cms-1": "cms-1",
        }
        reference = references.get(self.path)
        if reference is None:
            self._json(404, {"error": "not_found"})
            return
        self._record({})
        self._json(200, {"provider_reference": reference, "state": "unpublished"})

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


def _provision(client: TestClient, token: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/users",
        headers={"X-User-Provisioning-Token": token},
        json={},
    )
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _execute(
    client: TestClient,
    owner: dict[str, str],
    connector_id: str,
    *,
    method: str,
    path: str,
    body: dict[str, object] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, object]:
    request: dict[str, object] = {"method": method, "path": path}
    if body is not None:
        request["json_body"] = body
    if idempotency_key is not None:
        request["idempotency_key"] = idempotency_key
    response = client.post(
        f"/api/v1/connectors/{connector_id}/executions",
        headers=owner,
        json=request,
    )
    if response.status_code != 201:
        raise RuntimeError(f"business provider action failed with {response.status_code}")
    result = response.json()
    if result["execution"]["status"] != "completed":
        raise RuntimeError("business provider execution was not audited")
    return result


def main() -> None:
    select_disposable_runtime_database(settings)
    asyncio.run(_clean_disposable_database())
    provisioning_token = "b" * 43
    settings.USER_PROVISIONING_TOKEN_DIGEST = hashlib.sha256(
        provisioning_token.encode("utf-8")
    ).hexdigest()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _BusinessProviderHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    state_root = Path(settings.ASSET_STORAGE_ROOT).parent / "business-provider-smoke-state"
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
            owner = _provision(client, provisioning_token)
            foreign = _provision(client, provisioning_token)
            original_runtime = app.state.connector_runtime
            app.state.connector_runtime = runtime
            try:
                created = client.post(
                    "/api/v1/connectors",
                    headers=owner,
                    json={
                        "name": "Local business provider",
                        "provider": "local_protocol_test",
                        "service": "crm_social_cms_marketing",
                        "kind": "local_api",
                        "base_url": origin,
                        "auth_kind": "bearer",
                        "credential": _LOCAL_CREDENTIAL,
                        "scopes": ["read", "write"],
                        "capabilities": ["read"],
                        "path_prefixes": ["/api/"],
                        "health_path": "/api/health",
                        "discovery_path": "/api/capabilities",
                        "enabled": True,
                        "timeout_seconds": 2,
                        "max_retries": 1,
                        "rate_limit_requests_per_minute": 120,
                    },
                )
                created.raise_for_status()
                connector_id = created.json()["id"]
                health = client.post(
                    f"/api/v1/connectors/{connector_id}/health", headers=owner
                )
                health.raise_for_status()
                discovered = client.post(
                    f"/api/v1/connectors/{connector_id}/discover", headers=owner
                )
                discovered.raise_for_status()
                if discovered.json()["payload"] != {"capabilities": _CAPABILITIES}:
                    raise RuntimeError("business provider capability discovery failed")

                if client.post(
                    f"/api/v1/connectors/{connector_id}/executions",
                    headers=foreign,
                    json={"method": "GET", "path": "/api/crm/account"},
                ).status_code != 404:
                    raise RuntimeError("foreign owner could execute business connector")

                operations = (
                    ("GET", "/api/crm/account", None, None, "verified"),
                    ("POST", "/api/crm/contacts", {"display_name": "Disposable Contact"}, "crm-contact-create-1", "created"),
                    ("GET", "/api/crm/contacts/contact-1", None, None, "present"),
                    ("PATCH", "/api/crm/contacts/contact-1", {"status": "qualified"}, "crm-contact-update-1", "updated"),
                    ("POST", "/api/crm/notes", {"contact_reference": "contact-1", "note": "Disposable note"}, "crm-note-create-1", "created"),
                    ("POST", "/api/crm/tasks", {"contact_reference": "contact-1", "task": "Disposable follow-up"}, "crm-task-create-1", "created"),
                    ("POST", "/api/crm/deals", {"contact_reference": "contact-1", "stage": "test"}, "crm-deal-create-1", "created"),
                    ("GET", "/api/social/account", None, None, "verified"),
                    ("POST", "/api/social/drafts", {"content": "Private reversible test draft"}, "social-draft-create-1", "draft"),
                    ("GET", "/api/social/posts/social-1", None, None, "draft"),
                    ("PATCH", "/api/social/posts/social-1", {"content": "Updated private test draft"}, "social-draft-update-1", "updated"),
                    ("DELETE", "/api/social/posts/social-1", None, "social-unpublish-1", "unpublished"),
                    ("GET", "/api/cms/site", None, None, "verified"),
                    ("POST", "/api/cms/posts", {"title": "Private test draft"}, "cms-draft-create-1", "draft"),
                    ("GET", "/api/cms/posts/cms-1", None, None, "draft"),
                    ("PATCH", "/api/cms/posts/cms-1", {"title": "Updated private draft"}, "cms-draft-update-1", "updated"),
                    ("DELETE", "/api/cms/posts/cms-1", None, "cms-post-unpublish-1", "unpublished"),
                    ("GET", "/api/marketing/analytics", None, None, "available"),
                )
                audit_ids: set[str] = set()
                for method, path, body, key, expected_state in operations:
                    result = _execute(
                        client,
                        owner,
                        connector_id,
                        method=method,
                        path=path,
                        body=body,
                        idempotency_key=key,
                    )
                    if result["payload"].get("state") != expected_state:
                        raise RuntimeError("business provider result was not verified")
                    audit_ids.add(result["execution"]["id"])

                audits = client.get(
                    f"/api/v1/connectors/executions?connector_id={connector_id}&limit=100",
                    headers=owner,
                )
                audits.raise_for_status()
                recorded_ids = {item["id"] for item in audits.json()["items"]}
                if not audit_ids.issubset(recorded_ids):
                    raise RuntimeError("business provider audit trail is incomplete")

                revoked = client.delete(
                    f"/api/v1/connectors/{connector_id}", headers=owner
                )
                if revoked.status_code != 200 or revoked.json()["connection_status"] != "revoked":
                    raise RuntimeError("business provider revocation failed")
                denied = client.post(
                    f"/api/v1/connectors/{connector_id}/executions",
                    headers=owner,
                    json={"method": "GET", "path": "/api/crm/account"},
                )
                if denied.status_code != 409:
                    raise RuntimeError("revoked business provider still executed")
            finally:
                app.state.connector_runtime = original_runtime
                client.portal.call(runtime.close)
    finally:
        logging.getLogger().removeHandler(handler)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    if _LOCAL_CREDENTIAL in captured_logs.getvalue():
        raise RuntimeError("business provider credential leaked into logs")
    if len(_BusinessProviderHandler.requests) < 20:
        raise RuntimeError("business provider runtime coverage is incomplete")
    print("REAL_CRM_PROVIDER_PROTOCOL=passed")
    print("REAL_SOCIAL_CMS_PROVIDER_PROTOCOL=passed")
    print("BUSINESS_PROVIDER_OWNER_AUDIT_REVOCATION=passed")


if __name__ == "__main__":
    main()
