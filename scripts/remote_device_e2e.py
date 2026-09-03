#!/usr/bin/env python3
"""Verify the private tailnet and cross-device owner flow without retaining test data."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import os
from pathlib import Path
import socket
import ssl
import stat
import subprocess
import sys
import time
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import Settings  # noqa: E402


ACTIVATION_CONFIRMATION = "WORK_STATION_ALLOW_PRODUCTION_REMOTE_SMOKE"
PROVISIONING_TOKEN_PATH = Path.home() / ".ai_workspace_provisioning_token"
DESKTOP_LABEL = "Remote E2E desktop"
MOBILE_LABEL = "Remote E2E mobile"
INITIAL_SESSION_LABEL = "Provisioned owner session"
DESKTOP_WORKFLOW = "Remote E2E desktop to mobile"
MOBILE_WORKFLOW = "Remote E2E mobile to desktop"
TERMINAL_WORKFLOW_STATES = {"completed", "failed", "cancelled", "timed_out"}


class RemoteDeviceSmokeError(RuntimeError):
    """A redacted production remote-device validation failure."""


def _tailnet_host() -> str:
    try:
        completed = subprocess.run(
            [str(REPOSITORY_ROOT / "scripts/tailscale_cli.sh"), "status", "--json"],
            text=True,
            capture_output=True,
            check=True,
            timeout=10,
        )
        payload = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise RemoteDeviceSmokeError("private tailnet status is unavailable") from exc
    host = str(payload.get("Self", {}).get("DNSName", "")).rstrip(".")
    if (
        payload.get("BackendState") != "Running"
        or payload.get("Self", {}).get("Online") is not True
        or not host
    ):
        raise RemoteDeviceSmokeError("private tailnet is not online")
    return host


async def _pump(source: Any, destination: Any) -> None:
    try:
        while data := await source.read(65_536):
            destination.write(data)
            await destination.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        with suppress(Exception):
            destination.close()


async def _relay_connection(reader: Any, writer: Any, host: str) -> None:
    process = await asyncio.create_subprocess_exec(
        str(REPOSITORY_ROOT / "scripts/tailscale_cli.sh"),
        "nc",
        host,
        "443",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    assert process.stdin is not None and process.stdout is not None
    await asyncio.gather(
        _pump(reader, process.stdin),
        _pump(process.stdout, writer),
        return_exceptions=True,
    )
    if process.returncode is None:
        process.terminate()
    with suppress(ProcessLookupError):
        await process.wait()


class _RemoteClient:
    def __init__(self, host: str, relay_port: int) -> None:
        self.host = host
        self.relay_port = relay_port
        self.origin = f"https://{host}:{relay_port}"
        self.context = ssl.create_default_context()
        self._original_getaddrinfo = socket.getaddrinfo

    def __enter__(self) -> _RemoteClient:
        original = self._original_getaddrinfo
        host = self.host
        relay_port = self.relay_port

        def resolve_locally(
            name: str,
            port: Any,
            *args: Any,
            **kwargs: Any,
        ):
            if name == host and int(port) == relay_port:
                return original("127.0.0.1", port, *args, **kwargs)
            return original(name, port, *args, **kwargs)

        socket.getaddrinfo = resolve_locally
        return self

    def __exit__(self, *_args: object) -> None:
        socket.getaddrinfo = self._original_getaddrinfo

    def call(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        payload: Any = None,
        provisioning_token: str | None = None,
    ) -> tuple[int, dict[str, Any] | None, dict[str, str]]:
        headers = {"Accept": "application/json"}
        data = None
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if provisioning_token is not None:
            headers["X-User-Provisioning-Token"] = provisioning_token
        request = Request(self.origin + path, data=data, headers=headers, method=method)
        try:
            response = urlopen(request, context=self.context, timeout=20)
            raw = response.read()
            response_headers = {
                key.lower(): value for key, value in response.headers.items()
            }
            return (
                response.status,
                json.loads(raw) if raw else None,
                response_headers,
            )
        except HTTPError as exc:
            raw = exc.read()
            response_headers = {key.lower(): value for key, value in exc.headers.items()}
            return exc.code, json.loads(raw) if raw else None, response_headers
        except (OSError, TimeoutError, json.JSONDecodeError) as exc:
            raise RemoteDeviceSmokeError("private HTTPS request failed") from exc


async def _database_counts(database_url: str) -> tuple[int, int, int, int]:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT (SELECT count(*) FROM users), "
                        "(SELECT count(*) FROM user_sessions), "
                        "(SELECT count(*) FROM workflows), "
                        "(SELECT count(*) FROM tool_executions)"
                    )
                )
            ).one()
        return tuple(int(value) for value in row)
    except BaseException as exc:
        raise RemoteDeviceSmokeError("production count verification failed") from exc
    finally:
        await engine.dispose()


async def _remove_test_owner(database_url: str, owner_id: UUID) -> None:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with engine.begin() as connection:
            recent = await connection.scalar(
                text(
                    "SELECT count(*) FROM users WHERE id = :owner "
                    "AND created_at > now() - interval '30 minutes'"
                ),
                {"owner": owner_id},
            )
            labels = (
                await connection.execute(
                    text(
                        "SELECT label FROM user_sessions WHERE user_id = :owner "
                        "ORDER BY created_at"
                    ),
                    {"owner": owner_id},
                )
            ).scalars().all()
            names = (
                await connection.execute(
                    text(
                        "SELECT name FROM workflows WHERE owner_id = :owner "
                        "ORDER BY created_at"
                    ),
                    {"owner": owner_id},
                )
            ).scalars().all()
            workflow_count = await connection.scalar(
                text("SELECT count(*) FROM workflows WHERE owner_id = :owner"),
                {"owner": owner_id},
            )
            step_count = await connection.scalar(
                text("SELECT count(*) FROM workflow_steps WHERE owner_id = :owner"),
                {"owner": owner_id},
            )
            execution_count = await connection.scalar(
                text("SELECT count(*) FROM tool_executions WHERE owner_id = :owner"),
                {"owner": owner_id},
            )
            if (
                recent != 1
                or not set(labels).issubset(
                    {INITIAL_SESSION_LABEL, DESKTOP_LABEL, MOBILE_LABEL}
                )
                or len(labels) > 2
                or not set(names).issubset({DESKTOP_WORKFLOW, MOBILE_WORKFLOW})
                or len(names) > 2
                or workflow_count > 2
                or step_count > 2
                or execution_count > 2
            ):
                raise RemoteDeviceSmokeError("temporary owner cleanup identity failed")
            for table_name in (
                "assets",
                "conversations",
                "document_chunks",
                "documents",
                "memories",
                "memory_settings",
            ):
                count = await connection.scalar(
                    text(
                        f"SELECT count(*) FROM {table_name} WHERE owner_id = :owner"
                    ),
                    {"owner": owner_id},
                )
                if count != 0:
                    raise RemoteDeviceSmokeError(
                        "temporary owner cleanup scope is not exact"
                    )
            # Workflow steps reference tool executions, so workflows must be
            # deleted first. Both statements remain scoped to the test owner.
            await connection.execute(
                text("DELETE FROM workflows WHERE owner_id = :owner"),
                {"owner": owner_id},
            )
            await connection.execute(
                text("DELETE FROM tool_executions WHERE owner_id = :owner"),
                {"owner": owner_id},
            )
            deleted = await connection.execute(
                text("DELETE FROM users WHERE id = :owner"),
                {"owner": owner_id},
            )
            if deleted.rowcount != 1:
                raise RemoteDeviceSmokeError("temporary owner cleanup was not exact")
    except RemoteDeviceSmokeError:
        raise
    except BaseException as exc:
        raise RemoteDeviceSmokeError("temporary owner cleanup failed") from exc
    finally:
        await engine.dispose()


def _require(
    condition: bool,
    message: str,
) -> None:
    if not condition:
        raise RemoteDeviceSmokeError(message)


def _is_uniform_authentication_denial(
    status_code: int,
    body: dict[str, Any] | None,
    path: str,
) -> bool:
    return bool(
        status_code == 401
        and isinstance(body, dict)
        and set(body) == {"success", "error", "path", "timestamp"}
        and body.get("success") is False
        and body.get("error")
        == {
            "code": "HTTP_ERROR",
            "message": "Invalid authentication credentials",
        }
        and body.get("path") == path
        and isinstance(body.get("timestamp"), str)
    )


def _run_remote_flow(
    host: str,
    relay_port: int,
    state: dict[str, UUID],
) -> None:
    token_metadata = PROVISIONING_TOKEN_PATH.stat()
    _require(
        stat.S_ISREG(token_metadata.st_mode)
        and stat.S_IMODE(token_metadata.st_mode) == 0o600,
        "operator provisioning credential permissions are unsafe",
    )
    provisioning_token = PROVISIONING_TOKEN_PATH.read_text(encoding="utf-8").strip()
    _require(
        32 <= len(provisioning_token) <= 512,
        "operator provisioning credential is invalid",
    )
    desktop_token = ""
    mobile_token = ""
    try:
        with _RemoteClient(host, relay_port) as client:
            status_code, ready, _headers = client.call(
                "GET", "/api/v1/health/ready"
            )
            _require(
                status_code == 200 and ready is not None and ready.get("status") == "ready",
                "private HTTPS readiness did not pass",
            )
            status_code, body, headers = client.call(
                "POST",
                "/api/v1/users",
                payload={},
                provisioning_token=provisioning_token,
            )
            if status_code == 201 and body is not None and "id" in body:
                try:
                    state["owner_id"] = UUID(str(body["id"]))
                except ValueError as exc:
                    raise RemoteDeviceSmokeError(
                        "remote owner provisioning returned an invalid identity"
                    ) from exc
            _require(
                status_code == 201
                and body is not None
                and "no-store" in headers.get("cache-control", ""),
                "remote owner provisioning did not pass",
            )
            desktop_token = str(body["access_token"])
            provisioning_token = ""

            status_code, identity, _headers = client.call(
                "GET", "/api/v1/users/me", token=desktop_token
            )
            _require(
                status_code == 200
                and identity is not None
                and UUID(identity["id"]) == state["owner_id"],
                "remote desktop authentication did not pass",
            )
            status_code, diagnostics, _headers = client.call(
                "GET", "/api/v1/diagnostics", token=desktop_token
            )
            services = {
                item.get("id"): item.get("status")
                for item in diagnostics.get("services", [])
            } if isinstance(diagnostics, dict) else {}
            _require(
                status_code == 200 and diagnostics is not None,
                "authenticated production diagnostics request failed",
            )
            _require(
                diagnostics.get("mode") == "remote",
                "production diagnostics did not report private remote mode",
            )
            for service_name in (
                "backend",
                "database",
                "ollama",
                "storage",
                "remote_gateway",
                "gpu",
            ):
                _require(
                    services.get(service_name) == "ready",
                    f"production diagnostic unavailable: {service_name}",
                )
            _require(
                isinstance(diagnostics.get("agents"), dict),
                "agent monitoring diagnostic is unavailable",
            )
            _require(
                bool(diagnostics.get("models")) and bool(diagnostics.get("routes")),
                "model monitoring diagnostic is unavailable",
            )
            _require(
                diagnostics.get("hardware", {}).get("gpu_count", 0) >= 1
                and diagnostics.get("hardware", {}).get("runtime_validated") is True,
                "hardware monitoring diagnostic is unavailable",
            )
            _require(
                diagnostics.get("self_update", {}).get("configured") is True,
                "self-update monitoring diagnostic is unavailable",
            )
            serialized_diagnostics = json.dumps(
                diagnostics, sort_keys=True, separators=(",", ":")
            ).lower()
            _require(
                "/home/" not in serialized_diagnostics
                and "authorization" not in serialized_diagnostics
                and "access_token" not in serialized_diagnostics,
                "production diagnostics exposed private material",
            )
            status_code, connector_settings, headers = client.call(
                "GET", "/api/v1/connectors/settings", token=desktop_token
            )
            _require(
                status_code == 200
                and connector_settings is not None
                and connector_settings.get("configured") is True
                and "no-store" in headers.get("cache-control", ""),
                "connector monitor configuration did not pass",
            )
            status_code, connectors, _headers = client.call(
                "GET", "/api/v1/connectors", token=desktop_token
            )
            _require(
                status_code == 200
                and connectors is not None
                and connectors.get("items") == [],
                "temporary owner connector isolation did not pass",
            )
            status_code, renamed, _headers = client.call(
                "PATCH",
                "/api/v1/users/me/sessions/current",
                token=desktop_token,
                payload={"label": DESKTOP_LABEL},
            )
            _require(
                status_code == 200
                and renamed is not None
                and renamed.get("is_current") is True,
                "desktop device registration did not pass",
            )
            status_code, mobile, headers = client.call(
                "POST",
                "/api/v1/users/me/sessions",
                token=desktop_token,
                payload={"label": MOBILE_LABEL},
            )
            _require(
                status_code == 201
                and mobile is not None
                and "no-store" in headers.get("cache-control", ""),
                "mobile session issuance did not pass",
            )
            mobile_token = str(mobile["access_token"])
            mobile_session_id = str(mobile["session"]["id"])

            status_code, sessions, _headers = client.call(
                "GET", "/api/v1/users/me/sessions", token=mobile_token
            )
            _require(
                status_code == 200
                and sessions is not None
                and len(sessions.get("items", [])) == 2,
                "cross-device session listing did not pass",
            )

            def create_workflow(token: str, name: str, expression: str) -> str:
                status, workflow, _response_headers = client.call(
                    "POST",
                    "/api/v1/workflows",
                    token=token,
                    payload={
                        "name": name,
                        "steps": [
                            {
                                "tool_name": "calculator",
                                "arguments": {"expression": expression},
                            }
                        ],
                    },
                )
                _require(
                    status == 201
                    and workflow is not None
                    and workflow.get("status") == "pending",
                    "cross-device workflow creation did not pass",
                )
                return str(workflow["id"])

            def continue_workflow(token: str, workflow_id: str, expected: int) -> None:
                status, _started, _response_headers = client.call(
                    "POST", f"/api/v1/workflows/{workflow_id}/start", token=token
                )
                _require(status == 202, "cross-device workflow start did not pass")
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    status, workflow, _response_headers = client.call(
                        "GET", f"/api/v1/workflows/{workflow_id}", token=token
                    )
                    _require(
                        status == 200 and workflow is not None,
                        "cross-device workflow read did not pass",
                    )
                    if workflow.get("status") in TERMINAL_WORKFLOW_STATES:
                        result = workflow.get("steps", [{}])[0].get("result")
                        _require(
                            workflow.get("status") == "completed"
                            and result == {"value": expected},
                            "cross-device workflow verification did not pass",
                        )
                        return
                    time.sleep(0.05)
                raise RemoteDeviceSmokeError("cross-device workflow exceeded deadline")

            desktop_created = create_workflow(
                desktop_token, DESKTOP_WORKFLOW, "21*2"
            )
            status_code, visible, _headers = client.call(
                "GET", f"/api/v1/workflows/{desktop_created}", token=mobile_token
            )
            _require(
                status_code == 200
                and visible is not None
                and visible.get("status") == "pending",
                "desktop-to-mobile continuation visibility failed",
            )
            continue_workflow(mobile_token, desktop_created, 42)

            mobile_created = create_workflow(
                mobile_token, MOBILE_WORKFLOW, "7+5"
            )
            status_code, visible, _headers = client.call(
                "GET", f"/api/v1/workflows/{mobile_created}", token=desktop_token
            )
            _require(
                status_code == 200
                and visible is not None
                and visible.get("status") == "pending",
                "mobile-to-desktop continuation visibility failed",
            )
            continue_workflow(desktop_token, mobile_created, 12)

            status_code, _body, _headers = client.call(
                "DELETE",
                f"/api/v1/users/me/sessions/{mobile_session_id}",
                token=desktop_token,
            )
            _require(status_code == 204, "remote device revocation did not pass")
            status_code, denied, _headers = client.call(
                "GET", "/api/v1/users/me", token=mobile_token
            )
            _require(
                _is_uniform_authentication_denial(
                    status_code, denied, "/api/v1/users/me"
                ),
                "revoked mobile session was not denied safely",
            )
            status_code, _body, _headers = client.call(
                "DELETE", "/api/v1/users/me/sessions/current", token=desktop_token
            )
            _require(status_code == 204, "remote logout did not pass")
            status_code, denied, _headers = client.call(
                "GET", "/api/v1/users/me", token=desktop_token
            )
            _require(
                _is_uniform_authentication_denial(
                    status_code, denied, "/api/v1/users/me"
                ),
                "logged-out desktop session was not denied safely",
            )
    finally:
        provisioning_token = ""
        desktop_token = ""
        mobile_token = ""


async def _main() -> None:
    if os.environ.get(ACTIVATION_CONFIRMATION) != "YES":
        raise RemoteDeviceSmokeError(
            f"set {ACTIVATION_CONFIRMATION}=YES to permit the temporary production smoke"
        )
    configured = Settings(_env_file=BACKEND_ROOT / ".env")
    if configured.DATABASE_URL is None:
        raise RemoteDeviceSmokeError("production database configuration is unavailable")
    database_url = str(configured.DATABASE_URL)
    before = await _database_counts(database_url)
    host = _tailnet_host()
    server = await asyncio.start_server(
        lambda reader, writer: _relay_connection(reader, writer, host),
        "127.0.0.1",
        0,
    )
    relay_port = int(server.sockets[0].getsockname()[1])
    state: dict[str, UUID] = {}
    flow_error: BaseException | None = None
    cleanup_error: BaseException | None = None
    try:
        await asyncio.to_thread(_run_remote_flow, host, relay_port, state)
    except BaseException as exc:
        flow_error = exc
    finally:
        server.close()
        await server.wait_closed()
        if "owner_id" in state:
            try:
                await _remove_test_owner(database_url, state["owner_id"])
            except BaseException as exc:
                cleanup_error = exc
        after = await _database_counts(database_url)
        if after != before:
            cleanup_error = RemoteDeviceSmokeError(
                "production database counts changed after remote smoke cleanup"
            )
    if cleanup_error is not None:
        raise RemoteDeviceSmokeError("production remote-smoke cleanup failed") from None
    if flow_error is not None:
        if isinstance(flow_error, RemoteDeviceSmokeError):
            raise flow_error
        raise RemoteDeviceSmokeError("remote-device workflow failed safely") from None

    print("REMOTE_TAILNET_TLS_AND_AUTH=passed")
    print("DESKTOP_TO_MOBILE_WORKFLOW_CONTINUATION=passed")
    print("MOBILE_TO_DESKTOP_WORKFLOW_CONTINUATION=passed")
    print("REMOTE_DEVICE_REVOCATION_AND_LOGOUT=passed")
    print("PRODUCTION_MONITORING_DIAGNOSTICS=passed")
    print("CONNECTOR_MONITOR_OWNER_ISOLATION=passed")
    print("REMOTE_SMOKE_PRODUCTION_CLEANUP=passed")


def main() -> int:
    try:
        asyncio.run(_main())
    except (OSError, ValueError, RemoteDeviceSmokeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
