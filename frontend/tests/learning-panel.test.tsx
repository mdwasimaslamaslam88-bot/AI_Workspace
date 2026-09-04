import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { LearningCapabilities, LearningProgram } from "../src/api/contracts";
import { LearningPanel } from "../src/features/learning/LearningPanel";
import { rawSecret } from "./fixtures";

const programId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const lessonId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const activityId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const timestamp = "2026-09-03T00:00:00Z";

function program(ready = false): LearningProgram {
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
      difficulty: 1, status: ready ? "ready" : "planned", content: ready ? "Verified lesson" : null,
      output_sha256: ready ? "a".repeat(64) : null, model_id: ready ? "local/qwen3" : null,
      memory_context_count: 0, source_context_count: 0, grounding_state: "general_knowledge", score_bps: null, created_at: timestamp,
      generated_at: ready ? timestamp : null, completed_at: null,
      activities: ready ? [{ id: activityId, lesson_id: lessonId, kind: "revision", grading_mode: "exact", prompt: "Name the subject.", explanation_available_after_attempt: true, difficulty: 1, max_attempts: 3, skill_name: "Japanese", hints_available: 1, hints_requested: 0, source_context_count: 0, required: false, generated: false, model_id: null, attempts: [], created_at: timestamp }] : [],
    }],
  };
}

const capabilities: LearningCapabilities = { teacher_mode: true, speaking_partner: true, exam_mode: true, vocabulary_trainer: true, spaced_repetition: true, adaptive_assessment: true, rubric_grading: true, resumable_sessions: true, document_grounding: true, audit_history: true, mixed_language: true, pronunciation_scoring: false, pronunciation_status: "external_dependency", pronunciation_dependencies: ["pronunciation_scoring_provider"] };

function props(values: LearningProgram[] = [program()]) {
  return {
    onClose: vi.fn(), onCapabilities: vi.fn(async () => capabilities),
    onLoad: vi.fn(async () => values), onGet: vi.fn(async () => program(true)),
    onCreate: vi.fn(async () => program()), onGenerateLesson: vi.fn(async () => program(true)),
    onCreateActivity: vi.fn(async () => program(true)),
    onAttempt: vi.fn(async () => ({ id: "attempt", activity_id: activityId, is_correct: true, score_bps: 10_000, feedback: "Correct.", mistake_code: null, created_at: timestamp })),
    onCreateReviewItem: vi.fn(async () => ({ id: "review", front: "猫", back: "cat", interval_days: 0, ease_milli: 2_500, repetitions: 0, due_at: timestamp, last_quality: null, created_at: timestamp, updated_at: timestamp })),
    onReview: vi.fn(async () => ({ id: "review", front: "猫", back: "cat", interval_days: 1, ease_milli: 2_500, repetitions: 1, due_at: timestamp, last_quality: 4, created_at: timestamp, updated_at: timestamp })),
    onGenerateAssessment: vi.fn(async () => program(true)), onHint: vi.fn(async () => ({ hint: "Think of the lesson title.", remaining: 0 })),
    onUpdateProfile: vi.fn(async () => program(true)), onAttachSource: vi.fn(async () => program(true)), onDetachSource: vi.fn(async () => program(true)),
    onStartSession: vi.fn(async () => ({ id: "session", program_id: programId, current_lesson_id: null, mode: "teacher" as const, status: "active" as const, focus: "Japanese", planned_minutes: 30, interruption_count: 0, started_at: timestamp, last_activity_at: timestamp, paused_at: null, completed_at: null })),
    onTransitionSession: vi.fn(async () => ({ id: "session", program_id: programId, current_lesson_id: null, mode: "teacher" as const, status: "completed" as const, focus: "Japanese", planned_minutes: 30, interruption_count: 0, started_at: timestamp, last_activity_at: timestamp, paused_at: null, completed_at: timestamp })),
    onAnalytics: vi.fn(async () => ({ program_id: programId, mastery_bps: null, confidence_bps: 0, weak_topics: [], due_review_count: 0, current_streak_days: 0, best_streak_days: 0, active_session: null, skills: [] })),
    onStudyPlan: vi.fn(async () => ({ items: [{ date: "2026-09-03", minutes: 30, focus: "Foundations", mode: "teacher" as const }] })), onAudit: vi.fn(async () => ({ items: [] })),
  };
}

describe("LearningPanel", () => {
  it("creates a bounded multilingual adaptive curriculum", async () => {
    const actions = props([]);
    render(<LearningPanel {...actions} />);
    await userEvent.type(await screen.findByLabelText("Subject"), "Japanese");
    await userEvent.type(screen.getByLabelText("Goal"), "Advanced conversation");
    await userEvent.click(screen.getByRole("button", { name: "Create curriculum" }));
    expect(actions.onCreate).toHaveBeenCalledWith(expect.objectContaining({
      subject: "Japanese", goal: "Advanced conversation", target_language: "ja",
      instruction_language: "en", adaptive_difficulty: true,
    }), expect.any(AbortSignal));
  });

  it("generates a verified lesson and submits the untouched learner answer", async () => {
    const actions = props();
    render(<LearningPanel {...actions} />);
    await userEvent.click(await screen.findByRole("button", { name: "Generate verified lesson" }));
    await waitFor(() => expect(actions.onGenerateLesson).toHaveBeenCalledWith(programId, lessonId, expect.any(AbortSignal)));
    const practice = await screen.findByText("revision: Name the subject.");
    const form = practice.closest("form");
    expect(form).not.toBeNull();
    await userEvent.type(within(form as HTMLElement).getByLabelText("Your answer"), "Japanese");
    await userEvent.click(within(form as HTMLElement).getByRole("button", { name: "Check answer" }));
    await waitFor(() => expect(actions.onAttempt).toHaveBeenCalledWith(programId, activityId, { answer: "Japanese" }, expect.any(AbortSignal)));
  });

  it("reports the pronunciation boundary and redacts private failures", async () => {
    const actions = props([]);
    actions.onCreate.mockRejectedValueOnce(new Error(rawSecret));
    render(<LearningPanel {...actions} />);
    expect(await screen.findByText(/Pronunciation: external_dependency/)).toBeVisible();
    await userEvent.type(screen.getByLabelText("Subject"), "Japanese");
    await userEvent.type(screen.getByLabelText("Goal"), "Advanced conversation");
    await userEvent.click(screen.getByRole("button", { name: "Create curriculum" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("rejected or could not be verified");
    expect(document.body.textContent).not.toContain(rawSecret);
  });

  it("prevents duplicate answers after an activity is mastered", async () => {
    const mastered = program(true);
    mastered.lessons[0]!.activities[0]!.attempts.push({
      id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
      activity_id: activityId,
      is_correct: true,
      score_bps: 10_000,
      feedback: "Correct.",
      mistake_code: null,
      created_at: timestamp,
    });
    render(<LearningPanel {...props([mastered])} />);

    const practice = await screen.findByText("revision: Name the subject.");
    const form = practice.closest("form");
    expect(form).not.toBeNull();
    expect(within(form as HTMLElement).getByRole("button", { name: "Check answer" })).toBeDisabled();
  });
});
