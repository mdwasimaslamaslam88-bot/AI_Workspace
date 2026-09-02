import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type {
  Connector,
  ConnectorExecution,
  ConnectorExecutionRequest,
  ConnectorSettings,
  ConnectorWriteRequest,
} from "../src/api/contracts";
import { ConnectorPanel } from "../src/features/connectors/ConnectorPanel";
import { rawSecret } from "./fixtures";


const timestamp = "2026-09-02T00:00:00Z";
const connector: Connector = {
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
const execution: ConnectorExecution = {
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

function props() {
  return {
    onClose: vi.fn(),
    onLoadSettings: vi.fn(async (): Promise<ConnectorSettings> => ({
      configured: true,
      allowed_origins: [connector.base_url],
      supported_kinds: ["rest", "webhook", "local_api"],
      supported_auth_kinds: ["none", "bearer", "api_key", "oauth2_bearer"],
    })),
    onLoad: vi.fn(async () => [connector]),
    onLoadAudit: vi.fn(async () => [] as ConnectorExecution[]),
    onCreate: vi.fn(async (request: ConnectorWriteRequest) => {
      void request;
      return connector;
    }),
    onHealth: vi.fn(async () => ({ execution, payload: { healthy: true } })),
    onExecute: vi.fn(async (id: string, request: ConnectorExecutionRequest) => {
      void id;
      void request;
      return { execution, payload: { accepted: true } };
    }),
    onRevoke: vi.fn(async () => ({
      ...connector,
      enabled: false,
      credential_configured: false,
      connection_status: "revoked" as const,
      revoked_at: timestamp,
    })),
  };
}


describe("ConnectorPanel", () => {
  it("registers only an approved origin and never renders the credential", async () => {
    const actions = props();
    render(<ConnectorPanel {...actions} />);
    await screen.findByRole("option", { name: connector.base_url });

    await userEvent.type(screen.getByLabelText("Name"), "New app");
    await userEvent.selectOptions(screen.getByLabelText("Authentication"), "bearer");
    await userEvent.type(screen.getByLabelText("Credential"), rawSecret);
    await userEvent.click(screen.getByRole("button", { name: "Register connection" }));

    await waitFor(() => expect(actions.onCreate).toHaveBeenCalled());
    expect(actions.onCreate.mock.calls[0]![0]).toMatchObject({
      base_url: connector.base_url,
      credential: rawSecret,
      scopes: ["read"],
    });
    expect(screen.getByLabelText("Credential")).toHaveValue("");
    expect(document.body.textContent).not.toContain(rawSecret);
  });

  it("executes the exact scoped request and renders real returned payload", async () => {
    const actions = props();
    render(<ConnectorPanel {...actions} />);
    await screen.findByRole("option", { name: "Private API" });

    await userEvent.selectOptions(screen.getByLabelText("Method"), "POST");
    await userEvent.clear(screen.getByLabelText("Path"));
    await userEvent.type(screen.getByLabelText("Path"), "/v1/actions");
    fireEvent.change(screen.getByLabelText("JSON body"), {
      target: { value: '{"action":"sync"}' },
    });
    await userEvent.type(screen.getByLabelText("Idempotency key"), "sync-action-00001");
    await userEvent.click(screen.getByRole("button", { name: "Execute connected action" }));

    await waitFor(() => expect(actions.onExecute).toHaveBeenCalledWith(
      connector.id,
      {
        method: "POST",
        path: "/v1/actions",
        json_body: { action: "sync" },
        idempotency_key: "sync-action-00001",
      },
    ));
    expect(await screen.findByText(/"accepted": true/)).toBeVisible();
  });
});
