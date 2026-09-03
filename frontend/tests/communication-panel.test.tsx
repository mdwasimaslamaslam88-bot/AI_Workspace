import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { CommunicationCapabilities, Connector } from "../src/api/contracts";
import { CommunicationPanel } from "../src/features/communications/CommunicationPanel";


const connectorId = "11111111-1111-4111-8111-111111111111";
const capabilities: CommunicationCapabilities = {
  schema_version: 1,
  phone_call: {
    status: "external_dependency",
    configured: true,
    dependencies: ["telephony_provider", "owner_configuration"],
    connector_ids: [connectorId],
  },
  callback: {
    status: "external_dependency",
    configured: true,
    dependencies: ["telephony_provider", "owner_configuration"],
    connector_ids: [connectorId],
  },
  video: {
    status: "external_dependency",
    configured: false,
    dependencies: ["webrtc_provider", "owner_configuration"],
    connector_ids: [],
  },
  screen_share: {
    status: "external_dependency",
    configured: false,
    dependencies: ["webrtc_provider", "owner_configuration"],
    connector_ids: [],
  },
};

const connector = {
  id: connectorId,
  name: "Verified carrier gateway",
  provider: "Carrier",
} as Connector;

describe("CommunicationPanel", () => {
  it("shows provider success only after a matching API receipt", async () => {
    const user = userEvent.setup();
    const onStartPhoneCall = vi.fn(async () => ({
      request_id: "22222222-2222-4222-8222-222222222222",
      state: "accepted_by_provider" as const,
      connector_execution_id: "33333333-3333-4333-8333-333333333333",
    }));
    render(
      <CommunicationPanel
        onClose={vi.fn()}
        onConfigure={vi.fn()}
        onLoadCapabilities={vi.fn(async () => capabilities)}
        onLoadConnectors={vi.fn(async () => [connector])}
        onStartPhoneCall={onStartPhoneCall}
        onScheduleCallback={vi.fn()}
      />,
    );

    await screen.findByRole("option", { name: /Verified carrier gateway/ });
    await user.type(screen.getByLabelText("Destination (E.164)"), "+14155550123");
    await user.type(screen.getByLabelText("Purpose"), "Owner-approved appointment call");
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Place verified call" }));

    await waitFor(() => expect(onStartPhoneCall).toHaveBeenCalledWith({
      destination: "+14155550123",
      purpose: "Owner-approved appointment call",
      owner_approved: true,
      connector_id: connectorId,
    }, expect.any(AbortSignal)));
    expect(await screen.findByText(/matching acceptance receipt and the connector execution was audited/)).toBeInTheDocument();
    expect(screen.getByText("33333333-3333-4333-8333-333333333333")).toBeInTheDocument();
  });

  it("keeps an unconfigured provider explicit and routes to setup", async () => {
    const user = userEvent.setup();
    const onConfigure = vi.fn();
    const unavailable = {
      ...capabilities,
      phone_call: { ...capabilities.phone_call, configured: false, connector_ids: [] },
    };
    render(
      <CommunicationPanel
        onClose={vi.fn()}
        onConfigure={onConfigure}
        onLoadCapabilities={vi.fn(async () => unavailable)}
        onLoadConnectors={vi.fn(async () => [])}
        onStartPhoneCall={vi.fn()}
        onScheduleCallback={vi.fn()}
      />,
    );

    await user.click(await screen.findByRole("button", { name: "Configure communication gateway" }));
    expect(onConfigure).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "Place verified call" })).toBeDisabled();
  });
});
