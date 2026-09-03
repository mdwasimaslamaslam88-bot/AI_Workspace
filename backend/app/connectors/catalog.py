from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ConnectorPlatformSupportStatus(StrEnum):
    NATIVE = "native"
    ADAPTER_REQUIRED = "adapter_required"


@dataclass(frozen=True, slots=True)
class ConnectorPlatformCapability:
    id: str
    label: str
    status: ConnectorPlatformSupportStatus
    execution_mode: str
    requirement: str | None = None


CONNECTOR_LIFECYCLE = (
    "discover",
    "authenticate",
    "authorize",
    "health_check",
    "capability_discovery",
    "execute",
    "verify",
    "audit",
    "revoke",
    "reconnect",
)


CONNECTOR_PLATFORM_CAPABILITIES = (
    ConnectorPlatformCapability("rest", "REST", ConnectorPlatformSupportStatus.NATIVE, "bounded_json_http"),
    ConnectorPlatformCapability("graphql", "GraphQL", ConnectorPlatformSupportStatus.NATIVE, "bounded_json_http"),
    ConnectorPlatformCapability("webhooks", "Webhooks", ConnectorPlatformSupportStatus.NATIVE, "bounded_json_http"),
    ConnectorPlatformCapability("oauth2_oidc", "OAuth2 / OIDC", ConnectorPlatformSupportStatus.NATIVE, "encrypted_bearer_with_refresh"),
    ConnectorPlatformCapability("api_keys", "API keys", ConnectorPlatformSupportStatus.NATIVE, "encrypted_fixed_header"),
    ConnectorPlatformCapability("local_apis", "Local APIs", ConnectorPlatformSupportStatus.NATIVE, "loopback_only_json_http"),
    ConnectorPlatformCapability(
        "websocket",
        "WebSocket",
        ConnectorPlatformSupportStatus.ADAPTER_REQUIRED,
        "registered_adapter",
        "A bounded provider-specific message schema and authenticated adapter are required.",
    ),
    ConnectorPlatformCapability(
        "sse",
        "Server-sent events",
        ConnectorPlatformSupportStatus.ADAPTER_REQUIRED,
        "registered_adapter",
        "A bounded provider-specific event schema and reconnect policy are required.",
    ),
    ConnectorPlatformCapability(
        "sdks",
        "Provider SDKs",
        ConnectorPlatformSupportStatus.ADAPTER_REQUIRED,
        "registered_adapter",
        "Only reviewed, allowlisted SDK adapters may run; arbitrary SDK execution is denied.",
    ),
    ConnectorPlatformCapability(
        "databases",
        "Databases",
        ConnectorPlatformSupportStatus.ADAPTER_REQUIRED,
        "registered_adapter",
        "A least-privilege database adapter and owner-approved query contract are required.",
    ),
    ConnectorPlatformCapability(
        "browser_automation",
        "Browser automation",
        ConnectorPlatformSupportStatus.ADAPTER_REQUIRED,
        "registered_adapter",
        "A sandboxed browser adapter and explicit per-action authorization are required.",
    ),
    ConnectorPlatformCapability(
        "desktop_automation",
        "Desktop automation",
        ConnectorPlatformSupportStatus.ADAPTER_REQUIRED,
        "registered_adapter",
        "A platform sandbox, foreground consent policy, and bounded action adapter are required.",
    ),
    ConnectorPlatformCapability(
        "file_connectors",
        "File connectors",
        ConnectorPlatformSupportStatus.ADAPTER_REQUIRED,
        "registered_adapter",
        "An owner-approved filesystem root and traversal-safe adapter are required.",
    ),
)
