import { describe, expect, it, vi } from "vitest";

import { MobileApiClient } from "../src/api/client";

const experienceId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const timestamp = "2026-09-03T00:00:00Z";

function experience(withTurn = false) {
  return {
    id: experienceId, mode: "game", title: "Clockwork Vault", premise: "Escape the vault.",
    genre: "puzzle", language: "en", character_name: null, safety_tier: "general",
    status: "active", turn_count: withTurn ? 1 : 0, created_at: timestamp, updated_at: timestamp,
    completed_at: null, turns: withTurn ? [{ id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", position: 1,
      owner_input: "Inspect the lock.", output: "Three numbered rings turn.", output_sha256: "b".repeat(64),
      model_id: "local/qwen3", created_at: timestamp }] : [],
  };
}

describe("mobile creative API", () => {
  it("keeps experience identity in owner-scoped paths and input in request bodies", async () => {
    const calls: Array<{ path: string; method: string; body: unknown; authorization: string | null }> = [];
    const queue: unknown[] = [{ items: [experience()] }, experience(), experience(true), { ...experience(true), status: "completed", completed_at: timestamp }];
    const fetchMock = vi.fn(async (input: URL | RequestInfo, init?: RequestInit) => {
      calls.push({ path: new URL(input.toString()).pathname, method: init?.method ?? "GET", body: init?.body === undefined ? undefined : JSON.parse(String(init.body)), authorization: new Headers(init?.headers).get("Authorization") });
      return new Response(JSON.stringify(queue.shift()), { status: 200 });
    });
    const client = new MobileApiClient("private-mobile-token", { baseUrl: "https://work-station.example.ts.net", fetchImplementation: fetchMock });
    await client.listCreativeExperiences();
    await client.createCreativeExperience({ mode: "game", title: "Clockwork Vault", premise: "Escape the vault.", genre: "puzzle", language: "en", character_name: null });
    await client.addCreativeTurn(experienceId, { owner_input: "Inspect the lock." });
    await client.completeCreativeExperience(experienceId);
    expect(calls.map((call) => `${call.method} ${call.path}`)).toEqual([
      "GET /api/v1/creative/experiences", "POST /api/v1/creative/experiences",
      `POST /api/v1/creative/experiences/${experienceId}/turns`,
      `POST /api/v1/creative/experiences/${experienceId}/complete`,
    ]);
    expect(calls[2]?.body).toEqual({ owner_input: "Inspect the lock." });
    expect(calls.every((call) => call.authorization === "Bearer private-mobile-token")).toBe(true);
    expect(JSON.stringify(calls.map(({ authorization: _authorization, ...call }) => call))).not.toContain("private-mobile-token");
  });
});
