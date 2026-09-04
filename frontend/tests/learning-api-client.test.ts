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
    adaptive_difficulty: true, teaching_mode: "teacher", preferences: { explanation_style: "step_by_step", hints_before_answers: true, mixed_language: false, preferred_session_minutes: 30, pace: "balanced" }, status: "active", total_lessons: 1,
    completed_lessons: 0, total_attempts: 0, correct_attempts: 0,
    current_streak_days: 0, best_streak_days: 0, skills: [], sources: [],
    progress_bps: 0, accuracy_bps: null, created_at: timestamp, updated_at: timestamp,
    completed_at: null, review_items: [], lessons: [{
      id: lessonId, position: 1, title: "Foundations", objectives: ["Recognize basics"],
      difficulty: 1, status, content: status === "ready" ? "Verified lesson" : null,
      output_sha256: status === "ready" ? "a".repeat(64) : null,
      model_id: status === "ready" ? "local/qwen3" : null,
      memory_context_count: 0, source_context_count: 0, grounding_state: "general_knowledge", score_bps: null, created_at: timestamp,
      generated_at: status === "ready" ? timestamp : null, completed_at: null,
      activities: status === "ready" ? [{
        id: activityId, lesson_id: lessonId, kind: "revision", grading_mode: "exact",
        prompt: "Name the subject.", explanation_available_after_attempt: true,
        difficulty: 1, max_attempts: 3, skill_name: "Japanese", hints_available: 1,
        hints_requested: 0, source_context_count: 0, required: false, generated: false,
        model_id: null, attempts: [], created_at: timestamp,
      }] : [],
    }],
  };
}

const capabilities = {
  teacher_mode: true, speaking_partner: true, exam_mode: true,
  vocabulary_trainer: true, spaced_repetition: true, pronunciation_scoring: false,
  adaptive_assessment: true, rubric_grading: true, resumable_sessions: true,
  document_grounding: true, audit_history: true, mixed_language: true,
  pronunciation_status: "external_dependency",
  pronunciation_dependencies: ["pronunciation_scoring_provider"],
};

const attempt = {
  id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", activity_id: activityId,
  is_correct: true, score_bps: 10_000, feedback: "Correct.", mistake_code: null, created_at: timestamp,
};

const review = {
  id: reviewId, front: "猫", back: "cat", interval_days: 1,
  ease_milli: 2_500, repetitions: 1, due_at: timestamp, last_quality: 4,
  created_at: timestamp, updated_at: timestamp,
};

describe("learning API client", () => {
  it("uses only owner-scoped learning endpoints and preserves answer inputs", async () => {
    const session = { id: "ffffffff-ffff-4fff-8fff-ffffffffffff", program_id: programId, current_lesson_id: null, mode: "teacher", status: "active", focus: "Japanese", planned_minutes: 30, interruption_count: 0, started_at: timestamp, last_activity_at: timestamp, paused_at: null, completed_at: null };
    const queue = [capabilities, { items: [program()] }, program(), program(), program("ready"), program("ready"), attempt, review, review, program(), program("ready"), { hint: "Look at the title.", remaining: 0 }, program(), program(), session, { ...session, status: "paused", paused_at: timestamp, interruption_count: 1 }, { program_id: programId, mastery_bps: null, confidence_bps: 0, weak_topics: [], due_review_count: 0, current_streak_days: 1, best_streak_days: 1, active_session: session, skills: [] }, { items: [{ date: "2026-09-04", minutes: 30, focus: "Japanese", mode: "teacher" }] }, { items: [{ id: "99999999-9999-4999-8999-999999999999", action: "program_created", entity_kind: "program", entity_id: programId, metadata_sha256: "b".repeat(64), created_at: timestamp }] }];
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
    await client.updateLearningProfile(programId, { teaching_mode: "teacher", preferences: { explanation_style: "step_by_step", hints_before_answers: true, mixed_language: false, preferred_session_minutes: 30, pace: "balanced" } });
    await client.generateLearningAssessment(programId, lessonId);
    await client.requestLearningHint(programId, activityId);
    await client.attachLearningSource(programId, reviewId);
    await client.detachLearningSource(programId, reviewId);
    await client.startLearningSession(programId, { mode: "teacher", focus: "Japanese", planned_minutes: 30, current_lesson_id: null });
    await client.transitionLearningSession(programId, session.id, "pause");
    await client.getLearningAnalytics(programId);
    await client.getLearningStudyPlan(programId, 7);
    await client.getLearningAudit(programId);

    const calls = fetchImplementation.mock.calls as unknown as Array<[URL | RequestInfo, RequestInit?]>;
    expect(calls.map((call) => `${call[1]?.method ?? "GET"} ${new URL(call[0].toString()).pathname}`)).toEqual([
      "GET /api/v1/learning/capabilities", "GET /api/v1/learning/programs",
      "POST /api/v1/learning/programs", `GET /api/v1/learning/programs/${programId}`,
      `POST /api/v1/learning/programs/${programId}/lessons/${lessonId}/generate`,
      `POST /api/v1/learning/programs/${programId}/lessons/${lessonId}/activities`,
      `POST /api/v1/learning/programs/${programId}/activities/${activityId}/attempts`,
      `POST /api/v1/learning/programs/${programId}/review-items`,
      `POST /api/v1/learning/programs/${programId}/review-items/${reviewId}/reviews`,
      `PUT /api/v1/learning/programs/${programId}/profile`,
      `POST /api/v1/learning/programs/${programId}/lessons/${lessonId}/assessment`,
      `POST /api/v1/learning/programs/${programId}/activities/${activityId}/hint`,
      `POST /api/v1/learning/programs/${programId}/sources`,
      `DELETE /api/v1/learning/programs/${programId}/sources/${reviewId}`,
      `POST /api/v1/learning/programs/${programId}/sessions`,
      `POST /api/v1/learning/programs/${programId}/sessions/${session.id}/pause`,
      `GET /api/v1/learning/programs/${programId}/analytics`,
      `GET /api/v1/learning/programs/${programId}/study-plan`,
      `GET /api/v1/learning/programs/${programId}/audit`,
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
