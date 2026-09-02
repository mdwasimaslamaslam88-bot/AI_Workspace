import { describe, expect, it, vi } from "vitest";

import { ApiClient } from "../src/api/client";
import type { MarketingCampaignCreateRequest } from "../src/api/contracts";
import { jsonResponse, token } from "./fixtures";

const campaignId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const stageKinds = ["research", "strategy", "content", "creative", "approval", "publish", "analytics", "optimization"] as const;

function campaign() {
  return {
    id: campaignId,
    name: "Verified launch",
    objective: "Launch truthfully.",
    product: "AI OS",
    audience: "Technical founders",
    channels: ["email", "web"],
    source_facts: [{ source_reference: "brief.md#L1", fact: "AI OS is local-first." }],
    publisher_connector_id: null,
    publish_path: null,
    status: "pending",
    current_stage: null,
    analytics: null,
    error_code: null,
    created_at: "2026-09-02T00:00:00Z",
    updated_at: "2026-09-02T00:00:00Z",
    started_at: null,
    approved_at: null,
    published_at: null,
    completed_at: null,
    stages: stageKinds.map((kind, index) => ({
      id: `bbbbbbbb-bbbb-4bbb-8bbb-${String(index + 1).padStart(12, "0")}`,
      position: index + 1,
      kind,
      status: "pending",
      output: null,
      output_sha256: null,
      model_id: null,
      connector_execution_id: null,
      error_code: null,
      started_at: null,
      completed_at: null,
      duration_ms: null,
    })),
  };
}

describe("marketing API client", () => {
  it("uses the exact owner-scoped campaign lifecycle endpoints", async () => {
    const record = campaign();
    const fetchImplementation = vi.fn(
      async (input: URL | RequestInfo, init?: RequestInit) => {
        void input;
        void init;
        return jsonResponse(record);
      },
    );
    fetchImplementation.mockResolvedValueOnce(jsonResponse({ items: [record] }));
    const client = new ApiClient(token, { fetchImplementation: fetchImplementation as typeof fetch });
    const request: MarketingCampaignCreateRequest = {
      name: "Verified launch",
      objective: "Launch truthfully.",
      product: "AI OS",
      audience: "Technical founders",
      channels: ["email", "web"],
      source_facts: [{ source_reference: "brief.md#L1", fact: "AI OS is local-first." }],
    };
    const analytics = {
      source_reference: "provider.csv#row=2",
      observed_at: "2026-09-02T00:00:00Z",
      impressions: 100,
      clicks: 10,
      conversions: 1,
      spend_minor: 100,
      revenue_minor: 200,
    };

    await client.listMarketingCampaigns();
    await client.createMarketingCampaign(request);
    await client.getMarketingCampaign(campaignId);
    await client.startMarketingCampaign(campaignId);
    await client.approveMarketingCampaign(campaignId);
    await client.submitMarketingAnalytics(campaignId, analytics);
    await client.cancelMarketingCampaign(campaignId);

    expect(fetchImplementation.mock.calls.map((call) => call[0].toString())).toEqual([
      "http://127.0.0.1:8000/api/v1/marketing/campaigns",
      "http://127.0.0.1:8000/api/v1/marketing/campaigns",
      `http://127.0.0.1:8000/api/v1/marketing/campaigns/${campaignId}`,
      `http://127.0.0.1:8000/api/v1/marketing/campaigns/${campaignId}/start`,
      `http://127.0.0.1:8000/api/v1/marketing/campaigns/${campaignId}/approve`,
      `http://127.0.0.1:8000/api/v1/marketing/campaigns/${campaignId}/analytics`,
      `http://127.0.0.1:8000/api/v1/marketing/campaigns/${campaignId}`,
    ]);
    expect(JSON.parse(String(fetchImplementation.mock.calls[1]?.[1]?.body))).toEqual(request);
    expect(JSON.parse(String(fetchImplementation.mock.calls[5]?.[1]?.body))).toEqual(analytics);
    expect(fetchImplementation.mock.calls[6]?.[1]?.method).toBe("DELETE");
  });

  it("rejects reordered stage contracts", async () => {
    const invalid = campaign();
    invalid.stages.reverse();
    const client = new ApiClient(token, {
      fetchImplementation: vi.fn(async () => jsonResponse(invalid)) as typeof fetch,
    });

    await expect(client.getMarketingCampaign(campaignId)).rejects.toMatchObject({
      kind: "unexpected",
    });
  });
});
