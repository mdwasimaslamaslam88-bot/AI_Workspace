from __future__ import annotations

import asyncio
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import logging
from pathlib import Path
from threading import Thread
import time

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.clients.postgres import create_postgres_engine, dispose_postgres
from app.connectors import ConnectorCredentialBox, ConnectorRuntime
from app.core.config import settings
from app.main import app
from scripts.runtime_smoke_safety import select_disposable_runtime_database


_SOURCE_FACT = "The verified launch color is cobalt and early access begins in October."


class _PublisherHandler(BaseHTTPRequestHandler):
    payload: dict | None = None
    idempotency_key: str | None = None

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        type(self).payload = json.loads(self.rfile.read(length))
        type(self).idempotency_key = self.headers.get("Idempotency-Key")
        response = b'{"accepted":true}'
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

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


def main() -> None:
    select_disposable_runtime_database(settings)
    asyncio.run(_clean_disposable_database())
    provisioning_token = "m" * 43
    settings.USER_PROVISIONING_TOKEN_DIGEST = hashlib.sha256(
        provisioning_token.encode("utf-8")
    ).hexdigest()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _PublisherHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    state_root = Path(settings.ASSET_STORAGE_ROOT).parent / "marketing-smoke-state"
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
            original_marketing_runtime = app.state.marketing_campaign_runner.connector_runtime
            app.state.connector_runtime = runtime
            app.state.marketing_campaign_runner.connector_runtime = runtime
            try:
                connector_response = client.post(
                    "/api/v1/connectors",
                    headers=owner,
                    json={
                        "name": "Real marketing publisher",
                        "kind": "local_api",
                        "base_url": origin,
                        "auth_kind": "none",
                        "scopes": ["read", "write"],
                        "path_prefixes": ["/api/"],
                        "health_path": "/api/health",
                        "enabled": True,
                        "timeout_seconds": 2,
                        "max_retries": 0,
                        "rate_limit_requests_per_minute": 30,
                    },
                )
                connector_response.raise_for_status()
                connector_id = connector_response.json()["id"]
                created_response = client.post(
                    "/api/v1/marketing/campaigns",
                    headers=owner,
                    json={
                        "name": "Verified local launch",
                        "objective": "Prepare a truthful early-access launch campaign.",
                        "product": "AI OS",
                        "audience": "Technical founders",
                        "channels": ["email", "web"],
                        "source_facts": [
                            {
                                "source_reference": "launch-brief.md#facts",
                                "fact": _SOURCE_FACT,
                            }
                        ],
                        "publisher_connector_id": connector_id,
                        "publish_path": "/api/campaigns",
                    },
                )
                if created_response.status_code != 201:
                    raise RuntimeError("real marketing campaign was not created")
                campaign_id = created_response.json()["id"]
                if client.get(
                    f"/api/v1/marketing/campaigns/{campaign_id}", headers=foreign
                ).status_code != 404:
                    raise RuntimeError("foreign owner could inspect a campaign")
                started = client.post(
                    f"/api/v1/marketing/campaigns/{campaign_id}/start",
                    headers=owner,
                )
                if started.status_code != 202 or started.json()["status"] != "pending":
                    raise RuntimeError("real marketing campaign was not queued truthfully")
                deadline = time.monotonic() + 605
                campaign = started.json()
                while time.monotonic() < deadline:
                    response = client.get(
                        f"/api/v1/marketing/campaigns/{campaign_id}", headers=owner
                    )
                    response.raise_for_status()
                    campaign = response.json()
                    if campaign["status"] not in {"pending", "running"}:
                        break
                    time.sleep(0.1)
                if campaign["status"] != "needs_approval":
                    raise RuntimeError("real marketing generation did not reach approval")
                for stage in campaign["stages"][:4]:
                    output = stage["output"]
                    if (
                        stage["status"] != "completed"
                        or not output
                        or stage["output_sha256"]
                        != hashlib.sha256(output.encode("utf-8")).hexdigest()
                        or not stage["model_id"]
                    ):
                        raise RuntimeError("real marketing verifier evidence is incomplete")
                if _PublisherHandler.payload is not None:
                    raise RuntimeError("campaign published before owner approval")
                approved = client.post(
                    f"/api/v1/marketing/campaigns/{campaign_id}/approve",
                    headers=owner,
                )
                if approved.status_code != 200 or approved.json()["status"] != "awaiting_analytics":
                    raise RuntimeError("approved campaign was not published")
                if (
                    _PublisherHandler.payload is None
                    or _PublisherHandler.payload.get("campaign_id") != campaign_id
                    or _PublisherHandler.idempotency_key != f"marketing-{campaign_id}"
                ):
                    raise RuntimeError("real publisher contract is incomplete")
                completed = client.post(
                    f"/api/v1/marketing/campaigns/{campaign_id}/analytics",
                    headers=owner,
                    json={
                        "source_reference": "publisher-export.csv#row=2",
                        "observed_at": "2026-09-02T12:00:00Z",
                        "impressions": 10_000,
                        "clicks": 250,
                        "conversions": 10,
                        "spend_minor": 25_000,
                        "revenue_minor": 75_000,
                    },
                )
                if (
                    completed.status_code != 200
                    or completed.json()["status"] != "completed"
                    or completed.json()["analytics"]["return_on_ad_spend"] != "3.00"
                ):
                    raise RuntimeError("real marketing analytics were not grounded")
            finally:
                app.state.connector_runtime = original_runtime
                app.state.marketing_campaign_runner.connector_runtime = original_marketing_runtime
                client.portal.call(runtime.close)
    finally:
        logging.getLogger().removeHandler(handler)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    if _SOURCE_FACT in captured_logs.getvalue():
        raise RuntimeError("private marketing source leaked into logs")
    print("REAL_MARKETING_LOCAL_AGENTS=passed")
    print("MARKETING_OWNER_APPROVAL_AND_PUBLISH=passed")
    print("MARKETING_GROUNDED_ANALYTICS=passed")


if __name__ == "__main__":
    main()
