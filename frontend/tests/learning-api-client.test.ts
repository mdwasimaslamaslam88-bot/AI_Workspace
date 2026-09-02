import { describe, expect, it, vi } from "vitest";

import { ApiClient } from "../src/api/client";
import { jsonResponse, token } from "./fixtures";

const programId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const lessonId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const activityId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const reviewId = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
const timestamp = "2026-09-03T00:00:00Z";

function program(status: "planned" | "ready" = "planned") {
  return {
    id: programId, subject: "Japanese", goal: "Advanced conversation",
    target_language: "ja", instruction_language: "en", start_difficulty: 1,
    current_difficulty: 1, target_difficulty: 5, weekly_minutes: 150,
    adaptive_difficulty: true, status: "active", total_lessons: 1,
    completed_lessons: 0, total_attempts: 0, correct_attempts: 0,
    progress_bps: 0, accuracy_bps: null, created_at: timestamp, updated_at: timestamp,
    completed_at: null, review_items: [], lessons: [{
      id: lessonId, position: 1, title: "Foundations", objectives: ["Recognize basics"],
      difficulty: 1, status, content: status === "ready" ? "Verified lesson" : null,
      output_sha256: status === "ready" ? "a".repeat(64) : null,
      model_id: status === "ready" ? "local/qwen3" : null,
      memory_context_count: 0, score_bps: null, created_at: timestamp,
      generated_at: status === "ready" ? timestamp : null, completed_at: null,
      activities: status === "ready" ? [{
        id: activityId, lesson_id: lessonId, kind: "revision",
        prompt: "Name the subject.", explanation_available_after_attempt: true,
        difficulty: 1, max_attempts: 3, attempts: [], created_at: timestamp,
      }] : [],
    }],
  };
}

const capabilities = {
  teacher_mode: true, speaking_partner: true, exam_mode: true,
  vocabulary_trainer: true, spaced_repetition: true, pronunciation_scoring: false,
  pronunciation_status: "external_dependency",
  pronunciation_dependencies: ["pronunciation_scoring_provider"],
};

const attempt = {
  id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", activity_id: activityId,
  is_correct: true, score_bps: 10_000, feedback: "Correct.", created_at: timestamp,
};

const review = {
  id: reviewId, front: "猫", back: "cat", interval_days: 1,
  ease_milli: 2_500, repetitions: 1, due_at: timestamp, last_quality: 4,
  created_at: timestamp, updated_at: timestamp,
};

describe("learning API client", () => {
  it("uses only owner-scoped learning endpoints and preserves answer inputs", async () => {
    const queue = [capabilities, { items: [program()] }, program(), program(), program("ready"), program("ready"), attempt, review, review];
    const fetchImplementation = vi.fn(async () => jsonResponse(queue.shift()));
    const client = new ApiClient(token, { fetchImplementation: fetchImplementation as typeof fetch });

    await client.getLearningCapabilities();
    await client.listLearningPrograms();
    await client.createLearningProgram({ subject: "Japanese", goal: "Advanced conversation", target_language: "ja", instruction_language: "en", start_difficulty: 1, target_difficulty: 5, weekly_minutes: 150, adaptive_difficulty: true });
    await client.getLearningProgram(programId);
    await client.generateLearningLesson(programId, lessonId);
    await client.createLearningActivity(programId, lessonId, { kind: "quiz", prompt: "Translate 猫", expected_answer: "cat", explanation: "猫 means cat.", difficulty: 1, max_attempts: 3 });
    await client.submitLearningAttempt(programId, activityId, { answer: "cat" });
    await client.createLearningReviewItem(programId, { front: "猫", back: "cat" });
    await client.reviewLearningItem(programId, reviewId, { quality: 4 });

    const calls = fetchImplementation.mock.calls as unknown as Array<[URL | RequestInfo, RequestInit?]>;
    expect(calls.map((call) => `${call[1]?.method ?? "GET"} ${new URL(call[0].toString()).pathname}`)).toEqual([
      "GET /api/v1/learning/capabilities", "GET /api/v1/learning/programs",
      "POST /api/v1/learning/programs", `GET /api/v1/learning/programs/${programId}`,
      `POST /api/v1/learning/programs/${programId}/lessons/${lessonId}/generate`,
      `POST /api/v1/learning/programs/${programId}/lessons/${lessonId}/activities`,
      `POST /api/v1/learning/programs/${programId}/activities/${activityId}/attempts`,
      `POST /api/v1/learning/programs/${programId}/review-items`,
      `POST /api/v1/learning/programs/${programId}/review-items/${reviewId}/reviews`,
    ]);
    expect(JSON.parse(String(calls[6]?.[1]?.body))).toEqual({ answer: "cat" });
  });

  it("rejects a response that fabricates pronunciation availability", async () => {
    const client = new ApiClient(token, {
      fetchImplementation: vi.fn(async () => jsonResponse({ ...capabilities, pronunciation_scoring: true, pronunciation_status: "ready" })) as typeof fetch,
    });
    await expect(client.getLearningCapabilities()).rejects.toMatchObject({ kind: "unexpected" });
  });
});
