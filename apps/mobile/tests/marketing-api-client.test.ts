import type { MarketingCampaignCreateRequest } from "@work-station/shared";
import { describe, expect, it, vi } from "vitest";

import { MobileApiClient } from "../src/api/client";

const campaignId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const kinds = ["research", "strategy", "content", "creative", "approval", "publish", "analytics", "optimization"] as const;

function campaign() {
  return {
    id: campaignId,
    name: "Mobile launch",
    objective: "Launch truthfully.",
    product: "AI OS",
    audience: "Owners",
    channels: ["email"],
    source_facts: [{ source_reference: "brief.md", fact: "A grounded fact." }],
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
    stages: kinds.map((kind, index) => ({
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

describe("mobile marketing API", () => {
  it("keeps campaign identity in paths and source data in request bodies", async () => {
    const calls: Array<{ path: string; method: string; body: unknown }> = [];
    const record = campaign();
    let first = true;
    const fetchMock = vi.fn(async (input: URL | RequestInfo, init?: RequestInit) => {
      const path = new URL(input.toString()).pathname;
      calls.push({
        path,
        method: init?.method ?? "GET",
        body: init?.body === undefined ? undefined : JSON.parse(String(init.body)),
      });
      const body = first ? { items: [record] } : record;
      first = false;
      return new Response(JSON.stringify(body), { status: 200 });
    });
    const client = new MobileApiClient("mobile-session", {
      baseUrl: "https://work-station.example.ts.net",
      fetchImplementation: fetchMock,
    });
    const request: MarketingCampaignCreateRequest = {
      name: "Mobile launch",
      objective: "Launch truthfully.",
      product: "AI OS",
      audience: "Owners",
      channels: ["email"],
      source_facts: [{ source_reference: "brief.md", fact: "A grounded fact." }],
    };

    await client.listMarketingCampaigns();
    await client.createMarketingCampaign(request);
    await client.getMarketingCampaign(campaignId);
    await client.startMarketingCampaign(campaignId);
    await client.approveMarketingCampaign(campaignId);
    await client.submitMarketingAnalytics(campaignId, {
      source_reference: "provider.csv",
      observed_at: "2026-09-02T00:00:00Z",
      impressions: 10,
      clicks: 1,
      conversions: 0,
      spend_minor: 0,
      revenue_minor: 0,
    });
    await client.cancelMarketingCampaign(campaignId);

    expect(calls.map((call) => `${call.method} ${call.path}`)).toEqual([
      "GET /api/v1/marketing/campaigns",
      "POST /api/v1/marketing/campaigns",
      `GET /api/v1/marketing/campaigns/${campaignId}`,
      `POST /api/v1/marketing/campaigns/${campaignId}/start`,
      `POST /api/v1/marketing/campaigns/${campaignId}/approve`,
      `POST /api/v1/marketing/campaigns/${campaignId}/analytics`,
      `DELETE /api/v1/marketing/campaigns/${campaignId}`,
    ]);
    expect(calls[1]?.body).toEqual(request);
    expect(JSON.stringify(calls)).not.toContain("mobile-session");
  });
});
