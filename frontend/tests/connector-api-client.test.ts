import { describe, expect, it, vi } from "vitest";

import { ApiClient } from "../src/api/client";
import type { ConnectorWriteRequest } from "../src/api/contracts";
import { jsonResponse, token } from "./fixtures";


const timestamp = "2026-09-02T00:00:00Z";
const connector = {
  id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  name: "Private API",
  kind: "rest",
  base_url: "https://api.example.test",
  auth_kind: "bearer",
  credential_configured: true,
  scopes: ["read", "write"],
  path_prefixes: ["/v1/"],
  health_path: "/v1/health",
  enabled: true,
  connection_status: "ready",
  timeout_seconds: 5,
  max_retries: 1,
  rate_limit_requests_per_minute: 30,
  last_health_checked_at: null,
  created_at: timestamp,
  updated_at: timestamp,
  revoked_at: null,
};
const execution = {
  id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  connector_id: connector.id,
  action: "execute",
  method: "POST",
  path: "/v1/actions",
  status: "completed",
  attempts: 1,
  response_status_code: 200,
  request_body_sha256: "a".repeat(64),
  response_body_sha256: "b".repeat(64),
  response_bytes: 17,
  error_code: null,
  started_at: timestamp,
  completed_at: timestamp,
  duration_ms: 5,
};


describe("connector API client", () => {
  it("decodes settings, registry, and metadata-only audit contracts", async () => {
    const fetchImplementation = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({
        configured: true,
        allowed_origins: [connector.base_url],
        supported_kinds: ["rest", "webhook", "local_api"],
        supported_auth_kinds: ["none", "bearer", "api_key", "oauth2_bearer"],
      }))
      .mockResolvedValueOnce(jsonResponse({ items: [connector] }))
      .mockResolvedValueOnce(jsonResponse({ items: [execution] }));
    const client = new ApiClient(token, {
      fetchImplementation: fetchImplementation as typeof fetch,
    });

    await expect(client.getConnectorSettings()).resolves.toMatchObject({ configured: true });
    await expect(client.listConnectors()).resolves.toEqual({ items: [connector] });
    await expect(client.listConnectorExecutions({ limit: 10 })).resolves.toEqual({
      items: [execution],
    });
  });

  it("sends a write-only credential and a bounded connected action", async () => {
    const fetchImplementation = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(connector, 201))
      .mockResolvedValueOnce(jsonResponse({ execution, payload: { accepted: true } }, 201));
    const client = new ApiClient(token, {
      fetchImplementation: fetchImplementation as typeof fetch,
    });
    const request: ConnectorWriteRequest = {
      name: "Private API",
      kind: "rest" as const,
      base_url: connector.base_url,
      auth_kind: "bearer" as const,
      credential: "private-connector-token-123456",
      scopes: ["read", "write"],
      path_prefixes: ["/v1/"],
      health_path: "/v1/health",
      enabled: true,
    };

    await client.createConnector(request);
    await client.executeConnector(connector.id, {
      method: "POST",
      path: "/v1/actions",
      json_body: { action: "sync" },
      idempotency_key: "sync-action-00001",
    });

    expect(JSON.parse(String(fetchImplementation.mock.calls[0]![1]?.body))).toEqual(request);
    expect(JSON.parse(String(fetchImplementation.mock.calls[1]![1]?.body))).toEqual({
      method: "POST",
      path: "/v1/actions",
      json_body: { action: "sync" },
      idempotency_key: "sync-action-00001",
    });
    expect(new Headers(fetchImplementation.mock.calls[0]![1]?.headers).get("Authorization"))
      .toBe(`Bearer ${token}`);
  });

  it("rejects fabricated success without response evidence", async () => {
    const client = new ApiClient(token, {
      fetchImplementation: vi.fn(async () => jsonResponse({
        execution: { ...execution, response_body_sha256: null },
        payload: { accepted: true },
      })) as typeof fetch,
    });

    await expect(client.executeConnector(connector.id, {
      method: "POST", path: "/v1/actions",
    })).rejects.toMatchObject({ kind: "unexpected" });
  });
});
