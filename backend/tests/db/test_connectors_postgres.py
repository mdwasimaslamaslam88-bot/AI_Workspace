import asyncio
from collections import Counter
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.connectors.credentials import ConnectorCredentialBox
from app.connectors.runtime import ConnectorRuntime
from app.connectors.service import (
    ConnectorConflictError,
    ConnectorExecutionError,
    ConnectorService,
)
from app.models.connector import (
    Connector,
    ConnectorAction,
    ConnectorAuthKind,
    ConnectorExecution,
    ConnectorExecutionStatus,
    ConnectorKind,
)
from app.models.user import User


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_connector_end_to_end_is_owner_scoped_encrypted_audited_and_revocable(
    test_database_engine: AsyncEngine,
    tmp_path,
):
    calls = 0
    observed_authorization = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        observed_authorization.append(request.headers.get("authorization"))
        if request.url.path == "/v1/actions" and calls == 1:
            return httpx.Response(503, json={"retry": True})
        if request.url.path == "/v1/capabilities":
            return httpx.Response(
                200,
                json={"capabilities": ["campaign.publish", "read", "write"]},
            )
        return httpx.Response(
            200,
            json={"path": request.url.path, "method": request.method},
        )

    runtime = ConnectorRuntime(
        ConnectorCredentialBox(tmp_path / "connector-secrets"),
        ("https://connected.example.test",),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    factory = async_sessionmaker(test_database_engine, expire_on_commit=False)
    try:
        async with factory() as session:
            owner = User()
            foreign = User()
            session.add_all((owner, foreign))
            await session.commit()
            owner_id = owner.id
            foreign_id = foreign.id
            connector = await ConnectorService(session, runtime).create_for_owner(
                owner_id,
                name="Connected test app",
                provider="Loopback test",
                service="Campaign API",
                kind=ConnectorKind.REST,
                base_url="https://connected.example.test",
                auth_kind=ConnectorAuthKind.BEARER,
                credential="private-connector-token-123456",
                scopes=("read", "write"),
                capabilities=("read",),
                path_prefixes=("/v1/",),
                health_path="/v1/health",
                discovery_path="/v1/capabilities",
                enabled=True,
                timeout_seconds=2,
                max_retries=1,
                rate_limit_requests_per_minute=30,
            )
            assert connector.credential_configured is True
            assert await ConnectorService(session, runtime).list_for_owner(
                foreign_id
            ) == ()

            health = await ConnectorService(session, runtime).health_for_owner(
                owner_id, connector.id
            )
            assert health.execution.status is ConnectorExecutionStatus.COMPLETED
            assert health.payload == {"path": "/v1/health", "method": "GET"}

            discovered = await ConnectorService(session, runtime).discover_for_owner(
                owner_id, connector.id
            )
            assert discovered.execution.action is ConnectorAction.DISCOVER
            refreshed_connector = await ConnectorService(session, runtime).get_for_owner(
                owner_id, connector.id
            )
            assert refreshed_connector.capabilities == (
                "campaign.publish",
                "read",
                "write",
            )
            assert refreshed_connector.last_successful_test_at is not None
            assert refreshed_connector.audit_reference == discovered.execution.id

            calls = 0
            result = await ConnectorService(session, runtime).execute_for_owner(
                owner_id,
                connector.id,
                method="POST",
                path="/v1/actions",
                json_body={"action": "sync"},
                idempotency_key="sync-action-00001",
            )
            assert result.payload == {"path": "/v1/actions", "method": "POST"}
            assert result.execution.attempts == 2
            assert result.execution.request_body_sha256 is not None
            assert observed_authorization == [
                "Bearer private-connector-token-123456",
                "Bearer private-connector-token-123456",
                "Bearer private-connector-token-123456",
                "Bearer private-connector-token-123456",
            ]

            with pytest.raises(ConnectorExecutionError) as denied:
                await ConnectorService(session, runtime).execute_for_owner(
                    owner_id,
                    connector.id,
                    method="GET",
                    path="/private/admin",
                    json_body=None,
                    idempotency_key=None,
                )
            assert denied.value.execution.error_code == "connector_permission_denied"
            assert denied.value.execution.attempts == 0

            with pytest.raises(ConnectorExecutionError) as encoded_traversal:
                await ConnectorService(session, runtime).execute_for_owner(
                    owner_id,
                    connector.id,
                    method="GET",
                    path="/v1/%2e%2e/private",
                    json_body=None,
                    idempotency_key=None,
                )
            assert encoded_traversal.value.execution.error_code == (
                "connector_permission_denied"
            )
            assert encoded_traversal.value.execution.attempts == 0

            audits = await ConnectorService(session, runtime).list_executions_for_owner(
                owner_id, connector_id=connector.id
            )
            assert Counter(item.action for item in audits) == Counter(
                {
                    ConnectorAction.CONFIGURE: 1,
                    ConnectorAction.CREDENTIAL_CHANGE: 1,
                    ConnectorAction.PERMISSION_CHANGE: 1,
                    ConnectorAction.AUTHENTICATE: 1,
                    ConnectorAction.HEALTH: 1,
                    ConnectorAction.ACTIVATE: 1,
                    ConnectorAction.DISCOVER: 1,
                    ConnectorAction.EXECUTE: 3,
                }
            )
            assert Counter(item.status for item in audits) == Counter(
                {
                    ConnectorExecutionStatus.COMPLETED: 8,
                    ConnectorExecutionStatus.FAILED: 2,
                }
            )
            assert await ConnectorService(session, runtime).list_executions_for_owner(
                foreign_id
            ) == ()

            disconnected = await ConnectorService(session, runtime).disconnect_for_owner(
                owner_id, connector.id
            )
            assert disconnected.connection_status.value == "disabled"
            reconnected = await ConnectorService(session, runtime).reconnect_for_owner(
                owner_id, connector.id
            )
            assert reconnected.execution.action is ConnectorAction.HEALTH

            revoked = await ConnectorService(session, runtime).revoke_for_owner(
                owner_id, connector.id
            )
            assert revoked.connection_status.value == "revoked"
            persisted = (
                await session.execute(select(Connector).where(Connector.id == connector.id))
            ).scalar_one()
            assert persisted.credential_ciphertext is None
            lifecycle = await ConnectorService(
                session, runtime
            ).list_executions_for_owner(owner_id, connector_id=connector.id)
            assert Counter(item.action for item in lifecycle) == Counter(
                {
                    ConnectorAction.CONFIGURE: 1,
                    ConnectorAction.CREDENTIAL_CHANGE: 1,
                    ConnectorAction.PERMISSION_CHANGE: 1,
                    ConnectorAction.AUTHENTICATE: 2,
                    ConnectorAction.HEALTH: 2,
                    ConnectorAction.ACTIVATE: 2,
                    ConnectorAction.DISCOVER: 1,
                    ConnectorAction.EXECUTE: 3,
                    ConnectorAction.DISCONNECT: 1,
                    ConnectorAction.RECONNECT: 1,
                    ConnectorAction.REVOKE: 1,
                }
            )
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_connector_configuration_rotation_and_permission_changes_are_audited(
    test_database_engine: AsyncEngine,
    tmp_path,
):
    runtime = ConnectorRuntime(
        ConnectorCredentialBox(tmp_path / "connector-lifecycle-secrets"),
        ("https://lifecycle.example.test",),
        httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, json={})
            )
        ),
    )
    factory = async_sessionmaker(test_database_engine, expire_on_commit=False)
    first_credential = "first-private-credential-0001"
    rotated_credential = "rotated-private-credential-02"
    try:
        async with factory() as session:
            owner = User()
            session.add(owner)
            await session.commit()
            owner_id = owner.id
            connector = await ConnectorService(session, runtime).create_for_owner(
                owner_id,
                name="Lifecycle connector",
                provider="Lifecycle provider",
                service="Owner records",
                kind=ConnectorKind.REST,
                base_url="https://lifecycle.example.test",
                auth_kind=ConnectorAuthKind.BEARER,
                credential=first_credential,
                scopes=("read",),
                capabilities=("records.read",),
                path_prefixes=("/v1/",),
                health_path="/v1/health",
                enabled=False,
                timeout_seconds=2,
                max_retries=0,
                rate_limit_requests_per_minute=30,
            )
            updated = await ConnectorService(session, runtime).update_for_owner(
                owner.id,
                connector.id,
                name="Lifecycle connector",
                provider="Lifecycle provider",
                service="Owner records",
                kind=ConnectorKind.REST,
                base_url="https://lifecycle.example.test",
                auth_kind=ConnectorAuthKind.BEARER,
                credential=rotated_credential,
                scopes=("read", "write"),
                capabilities=("records.read", "records.write"),
                path_prefixes=("/v1/",),
                health_path="/v1/health",
                discovery_path=None,
                enabled=False,
                timeout_seconds=2,
                max_retries=0,
                rate_limit_requests_per_minute=30,
            )
            assert updated.credential_configured is True
            audits = await ConnectorService(session, runtime).list_executions_for_owner(
                owner.id, connector_id=connector.id
            )
            assert Counter(item.action for item in audits) == Counter(
                {
                    ConnectorAction.CONFIGURE: 2,
                    ConnectorAction.CREDENTIAL_CHANGE: 2,
                    ConnectorAction.PERMISSION_CHANGE: 2,
                }
            )
            assert all(item.attempts == 0 for item in audits)
            assert all(item.response_bytes == 0 for item in audits)
            assert all(item.request_body_sha256 is not None for item in audits)
            assert all(
                first_credential not in (item.path, item.error_code or "")
                and rotated_credential not in (item.path, item.error_code or "")
                for item in audits
            )
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_failed_authenticated_health_is_audited_and_never_activates(
    test_database_engine: AsyncEngine,
    tmp_path,
):
    calls = 0

    async def reject_credential(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error": "unauthorized"})

    runtime = ConnectorRuntime(
        ConnectorCredentialBox(tmp_path / "connector-auth-failure-secrets"),
        ("https://auth-failure.example.test",),
        httpx.AsyncClient(transport=httpx.MockTransport(reject_credential)),
    )
    factory = async_sessionmaker(test_database_engine, expire_on_commit=False)
    try:
        async with factory() as session:
            owner = User()
            session.add(owner)
            await session.commit()
            owner_id = owner.id
            connector = await ConnectorService(session, runtime).create_for_owner(
                owner_id,
                name="Rejected credential connector",
                provider="Authentication test",
                service="Owner records",
                kind=ConnectorKind.REST,
                base_url="https://auth-failure.example.test",
                auth_kind=ConnectorAuthKind.BEARER,
                credential="rejected-private-credential-01",
                scopes=("read",),
                capabilities=("records.read",),
                path_prefixes=("/v1/",),
                health_path="/v1/health",
                enabled=False,
                timeout_seconds=2,
                max_retries=0,
                rate_limit_requests_per_minute=30,
            )
            with pytest.raises(ConnectorExecutionError) as health_failure:
                await ConnectorService(session, runtime).reconnect_for_owner(
                    owner_id, connector.id
                )
            assert health_failure.value.execution.error_code == "connector_http_error"
            current = await ConnectorService(session, runtime).get_for_owner(
                owner_id, connector.id
            )
            assert current.connection_status.value == "unavailable"

            with pytest.raises(ConnectorExecutionError) as blocked_execution:
                await ConnectorService(session, runtime).execute_for_owner(
                    owner_id,
                    connector.id,
                    method="GET",
                    path="/v1/records",
                    json_body=None,
                    idempotency_key=None,
                )
            assert blocked_execution.value.execution.error_code == "connector_disabled"
            assert calls == 1

            audits = await ConnectorService(session, runtime).list_executions_for_owner(
                owner_id, connector_id=connector.id
            )
            actions = Counter(item.action for item in audits)
            assert actions[ConnectorAction.AUTHENTICATE] == 1
            assert actions[ConnectorAction.HEALTH] == 1
            assert actions[ConnectorAction.ACTIVATE] == 0
            assert actions[ConnectorAction.RECONNECT] == 1
            assert actions[ConnectorAction.EXECUTE] == 1
            assert all(
                item.status is ConnectorExecutionStatus.FAILED
                for item in audits
                if item.action
                in {
                    ConnectorAction.AUTHENTICATE,
                    ConnectorAction.HEALTH,
                    ConnectorAction.RECONNECT,
                    ConnectorAction.EXECUTE,
                }
            )
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_database_rejects_connector_audit_with_a_different_owner(
    test_database_engine: AsyncEngine,
):
    from datetime import datetime, timezone
    from sqlalchemy.exc import IntegrityError

    factory = async_sessionmaker(test_database_engine, expire_on_commit=False)
    async with factory() as session:
        owner = User()
        foreign = User()
        session.add_all((owner, foreign))
        await session.commit()
        connector = Connector(
            owner_id=owner.id,
            name="Owned",
            kind=ConnectorKind.REST,
            base_url="https://owned.example.test",
            auth_kind=ConnectorAuthKind.NONE,
            credential_ciphertext=None,
            scopes_json='["read"]',
            path_prefixes_json='["/v1/"]',
            health_path="/v1/health",
            enabled=True,
            timeout_seconds=2,
            max_retries=0,
            rate_limit_requests_per_minute=30,
        )
        session.add(connector)
        await session.commit()
        now = datetime.now(timezone.utc)
        execution = ConnectorExecution(
            id=uuid4(),
            connector_id=connector.id,
            owner_id=foreign.id,
            action="execute",
            method="GET",
            path="/v1/status",
            status=ConnectorExecutionStatus.FAILED,
            attempts=0,
            error_code="connector_permission_denied",
            started_at=now,
            completed_at=now,
            duration_ms=0,
        )
        session.add(execution)
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_concurrent_connector_creates_cannot_exceed_owner_cap(
    test_database_engine: AsyncEngine,
    tmp_path,
):
    runtime = ConnectorRuntime(
        ConnectorCredentialBox(tmp_path / "connector-cap-secrets"),
        ("https://connected.example.test",),
        httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: None)),
    )
    factory = async_sessionmaker(test_database_engine, expire_on_commit=False)

    async def create(session, owner_id, name):
        return await ConnectorService(session, runtime).create_for_owner(
            owner_id,
            name=name,
            kind=ConnectorKind.REST,
            base_url="https://connected.example.test",
            auth_kind=ConnectorAuthKind.NONE,
            credential=None,
            scopes=("read",),
            path_prefixes=("/v1/",),
            health_path="/v1/health",
            enabled=False,
            timeout_seconds=2,
            max_retries=0,
            rate_limit_requests_per_minute=30,
        )

    try:
        async with factory() as session:
            owner = User()
            session.add(owner)
            await session.commit()
            owner_id = owner.id
            for index in range(31):
                await create(session, owner_id, f"Existing connector {index}")

        async def concurrent_create(name: str):
            async with factory() as session:
                return await create(session, owner_id, name)

        results = await asyncio.gather(
            concurrent_create("Concurrent connector A"),
            concurrent_create("Concurrent connector B"),
            return_exceptions=True,
        )
        assert sum(not isinstance(result, BaseException) for result in results) == 1
        assert sum(isinstance(result, ConnectorConflictError) for result in results) == 1
        async with factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(Connector)
                .where(Connector.owner_id == owner_id)
            )
            assert count == 32
    finally:
        await runtime.close()
