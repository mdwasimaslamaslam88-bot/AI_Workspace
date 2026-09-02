import { describe, expect, it, vi } from "vitest";

import { MobileApiClient } from "../src/api/client";

const programId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const lessonId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const activityId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const timestamp = "2026-09-03T00:00:00Z";

function program() {
  return {
    id: programId, subject: "Japanese", goal: "Conversation", target_language: "ja",
    instruction_language: "en", start_difficulty: 1, current_difficulty: 1,
    target_difficulty: 5, weekly_minutes: 150, adaptive_difficulty: true,
    status: "active", total_lessons: 1, completed_lessons: 0, total_attempts: 0,
    correct_attempts: 0, progress_bps: 0, accuracy_bps: null, review_items: [],
    created_at: timestamp, updated_at: timestamp, completed_at: null, lessons: [{
      id: lessonId, position: 1, title: "Foundations", objectives: ["Basics"],
      difficulty: 1, status: "planned", content: null, output_sha256: null,
      model_id: null, memory_context_count: 0, score_bps: null, activities: [],
      created_at: timestamp, generated_at: null, completed_at: null,
    }],
  };
}

describe("mobile learning API", () => {
  it("keeps program identity in owner-scoped paths and exact answers in bodies", async () => {
    const calls: Array<{ path: string; method: string; body: unknown }> = [];
    const queue: unknown[] = [{ items: [program()] }, program(), program(), { id: "attempt", activity_id: activityId, is_correct: true, score_bps: 10_000, feedback: "Correct.", created_at: timestamp }];
    const fetchMock = vi.fn(async (input: URL | RequestInfo, init?: RequestInit) => {
      calls.push({ path: new URL(input.toString()).pathname, method: init?.method ?? "GET", body: init?.body === undefined ? undefined : JSON.parse(String(init.body)) });
      return new Response(JSON.stringify(queue.shift()), { status: 200 });
    });
    const client = new MobileApiClient("private-mobile-token", { baseUrl: "https://work-station.example.ts.net", fetchImplementation: fetchMock });
    await client.listLearningPrograms();
    await client.createLearningProgram({ subject: "Japanese", goal: "Conversation", target_language: "ja", instruction_language: "en", start_difficulty: 1, target_difficulty: 5, weekly_minutes: 150, adaptive_difficulty: true });
    await client.generateLearningLesson(programId, lessonId);
    await client.submitLearningAttempt(programId, activityId, { answer: "Japanese" });
    expect(calls.map((call) => `${call.method} ${call.path}`)).toEqual([
      "GET /api/v1/learning/programs", "POST /api/v1/learning/programs",
      `POST /api/v1/learning/programs/${programId}/lessons/${lessonId}/generate`,
      `POST /api/v1/learning/programs/${programId}/activities/${activityId}/attempts`,
    ]);
    expect(calls[3]?.body).toEqual({ answer: "Japanese" });
    expect(JSON.stringify(calls)).not.toContain("private-mobile-token");
  });
});
