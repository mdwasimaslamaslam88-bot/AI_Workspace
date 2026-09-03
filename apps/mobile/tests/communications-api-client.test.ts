import { describe, expect, it, vi } from "vitest";

import { MobileApiClient } from "../src/api/client";


describe("mobile communication API", () => {
  it("discovers eligible connectors and sends approval only in the request body", async () => {
    const connectorId = "11111111-1111-4111-8111-111111111111";
    const requestId = "22222222-2222-4222-8222-222222222222";
    const executionId = "33333333-3333-4333-8333-333333333333";
    const fetchMock = vi.fn(async (input: URL | RequestInfo, init?: RequestInit) => {
      const url = String(input);
      expect(url).not.toContain("+14155550123");
      if (url.endsWith("/capabilities")) {
        return new Response(JSON.stringify({
          schema_version: 1,
          phone_call: { status: "external_dependency", configured: true, dependencies: ["telephony_provider"], connector_ids: [connectorId] },
          callback: { status: "external_dependency", configured: true, dependencies: ["telephony_provider"], connector_ids: [connectorId] },
          video: { status: "external_dependency", configured: false, dependencies: ["webrtc_provider"], connector_ids: [] },
          screen_share: { status: "external_dependency", configured: false, dependencies: ["webrtc_provider"], connector_ids: [] },
        }));
      }
      expect(init?.method).toBe("POST");
      expect(JSON.parse(String(init?.body))).toEqual({
        destination: "+14155550123",
        purpose: "Owner-approved appointment call",
        owner_approved: true,
        connector_id: connectorId,
      });
      return new Response(JSON.stringify({
        request_id: requestId,
        state: "accepted_by_provider",
        connector_execution_id: executionId,
      }), { status: 202 });
    });
    const client = new MobileApiClient("mobile-session", {
      baseUrl: "https://work-station.example.ts.net",
      fetchImplementation: fetchMock,
    });

    await expect(client.getCommunicationCapabilities()).resolves.toMatchObject({
      phone_call: { configured: true, connector_ids: [connectorId] },
    });
    await expect(client.startPhoneCall({
      destination: "+14155550123",
      purpose: "Owner-approved appointment call",
      owner_approved: true,
      connector_id: connectorId,
    })).resolves.toEqual({
      request_id: requestId,
      state: "accepted_by_provider",
      connector_execution_id: executionId,
    });
  });
});
