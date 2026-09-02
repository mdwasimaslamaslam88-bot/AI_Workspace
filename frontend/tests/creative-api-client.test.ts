import { describe, expect, it, vi } from "vitest";

import { ApiClient } from "../src/api/client";
import { jsonResponse, token } from "./fixtures";

const experienceId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const timestamp = "2026-09-03T00:00:00Z";

function experience(withTurn = false) {
  return {
    id: experienceId, mode: "story", title: "The Signal", premise: "A quiet observatory.",
    genre: "science fiction", language: "en", character_name: null, safety_tier: "general",
    status: "active", turn_count: withTurn ? 1 : 0, created_at: timestamp, updated_at: timestamp,
    completed_at: null, turns: withTurn ? [{ id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", position: 1,
      owner_input: "Open the door.", output: "The observatory door opens.", output_sha256: "a".repeat(64),
      model_id: "local/qwen3", created_at: timestamp }] : [],
  };
}

const capabilities = {
  interactive_stories: true, text_games: true, fictional_characters: true,
  verified_local_text_generation: true, general_audience_only: true,
  image_generation_status: "runtime_dependent", voice_status: "runtime_dependent",
  video_generation_status: "external_dependency", animation_status: "external_dependency",
  audio_generation_editing_status: "external_dependency", adult_experience_status: "external_dependency",
  external_dependencies: ["verified_video_animation_or_audio_runtime", "jurisdiction_check", "age_verification", "consent_policy"],
};

describe("creative API client", () => {
  it("uses owner-scoped paths and preserves untouched owner input", async () => {
    const queue = [capabilities, { items: [experience()] }, experience(), experience(), experience(true), { ...experience(true), status: "completed", completed_at: timestamp }];
    const fetchImplementation = vi.fn(async () => jsonResponse(queue.shift()));
    const client = new ApiClient(token, { fetchImplementation: fetchImplementation as typeof fetch });

    await client.getCreativeCapabilities();
    await client.listCreativeExperiences();
    await client.createCreativeExperience({ mode: "story", title: "The Signal", premise: "A quiet observatory.", genre: "science fiction", language: "en", character_name: null });
    await client.getCreativeExperience(experienceId);
    await client.addCreativeTurn(experienceId, { owner_input: "Open the door." });
    await client.completeCreativeExperience(experienceId);

    const calls = fetchImplementation.mock.calls as unknown as Array<[URL | RequestInfo, RequestInit?]>;
    expect(calls.map((call) => `${call[1]?.method ?? "GET"} ${new URL(call[0].toString()).pathname}`)).toEqual([
      "GET /api/v1/creative/capabilities", "GET /api/v1/creative/experiences",
      "POST /api/v1/creative/experiences", `GET /api/v1/creative/experiences/${experienceId}`,
      `POST /api/v1/creative/experiences/${experienceId}/turns`,
      `POST /api/v1/creative/experiences/${experienceId}/complete`,
    ]);
    expect(JSON.parse(String(calls[4]?.[1]?.body))).toEqual({ owner_input: "Open the door." });
  });

  it("rejects fabricated advanced-media readiness and invalid artifact integrity", async () => {
    const capabilityClient = new ApiClient(token, { fetchImplementation: vi.fn(async () => jsonResponse({ ...capabilities, video_generation_status: "ready" })) as typeof fetch });
    await expect(capabilityClient.getCreativeCapabilities()).rejects.toMatchObject({ kind: "unexpected" });
    const artifactClient = new ApiClient(token, { fetchImplementation: vi.fn(async () => jsonResponse({ ...experience(true), turns: [{ ...experience(true).turns[0], output_sha256: "invalid" }] })) as typeof fetch });
    await expect(artifactClient.getCreativeExperience(experienceId)).rejects.toMatchObject({ kind: "unexpected" });
  });
});
