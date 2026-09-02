import { type FormEvent, useEffect, useMemo, useState } from "react";

import type {
  LearningActivityCreateRequest,
  LearningAttempt,
  LearningAttemptRequest,
  LearningCapabilities,
  LearningProgram,
  LearningProgramCreateRequest,
  LearningReviewItem,
  LearningReviewItemCreateRequest,
  LearningReviewRequest,
} from "../../api/contracts";


interface LearningPanelProps {
  onClose: () => void;
  onCapabilities: (signal?: AbortSignal) => Promise<LearningCapabilities>;
  onLoad: (signal?: AbortSignal) => Promise<LearningProgram[]>;
  onGet: (id: string, signal?: AbortSignal) => Promise<LearningProgram>;
  onCreate: (request: LearningProgramCreateRequest, signal?: AbortSignal) => Promise<LearningProgram>;
  onGenerateLesson: (programId: string, lessonId: string, signal?: AbortSignal) => Promise<LearningProgram>;
  onCreateActivity: (programId: string, lessonId: string, request: LearningActivityCreateRequest, signal?: AbortSignal) => Promise<LearningProgram>;
  onAttempt: (programId: string, activityId: string, request: LearningAttemptRequest, signal?: AbortSignal) => Promise<LearningAttempt>;
  onCreateReviewItem: (programId: string, request: LearningReviewItemCreateRequest, signal?: AbortSignal) => Promise<LearningReviewItem>;
  onReview: (programId: string, itemId: string, request: LearningReviewRequest, signal?: AbortSignal) => Promise<LearningReviewItem>;
}

function replaceProgram(current: LearningProgram[], value: LearningProgram) {
  return [value, ...current.filter((program) => program.id !== value.id)].sort(
    (left, right) => right.updated_at.localeCompare(left.updated_at),
  );
}

function boundedInteger(value: FormDataEntryValue | null, minimum: number, maximum: number) {
  const result = Number(value);
  if (!Number.isSafeInteger(result) || result < minimum || result > maximum) {
    throw new Error("A bounded whole number is required.");
  }
  return result;
}

export function LearningPanel({
  onClose, onCapabilities, onLoad, onGet, onCreate, onGenerateLesson,
  onCreateActivity, onAttempt, onCreateReviewItem, onReview,
}: LearningPanelProps) {
  const [programs, setPrograms] = useState<LearningProgram[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [capabilities, setCapabilities] = useState<LearningCapabilities | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const selected = useMemo(
    () => programs.find((program) => program.id === selectedId) ?? programs[0] ?? null,
    [programs, selectedId],
  );

  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([onLoad(controller.signal), onCapabilities(controller.signal)])
      .then(([values, available]) => {
        if (controller.signal.aborted) return;
        setPrograms(values);
        setCapabilities(available);
        setSelectedId(values[0]?.id ?? null);
      })
      .catch(() => {
        if (!controller.signal.aborted) setNotice("Learning workspace could not be loaded.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [onCapabilities, onLoad]);

  async function perform<T>(operation: (signal: AbortSignal) => Promise<T>): Promise<T | undefined> {
    if (busy) return;
    const controller = new AbortController();
    setBusy(true);
    setNotice(null);
    try {
      return await operation(controller.signal);
    } catch {
      setNotice("The learning action was rejected or could not be verified.");
    } finally {
      setBusy(false);
    }
  }

  async function refresh(programId: string, signal?: AbortSignal) {
    const value = await onGet(programId, signal);
    setPrograms((current) => replaceProgram(current, value));
    setSelectedId(programId);
  }

  async function createProgram(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    const value = await perform((signal) => onCreate({
      subject: String(values.get("subject") ?? "").trim(),
      goal: String(values.get("goal") ?? "").trim(),
      target_language: String(values.get("target_language") ?? "").trim(),
      instruction_language: String(values.get("instruction_language") ?? "").trim(),
      start_difficulty: boundedInteger(values.get("start_difficulty"), 1, 5),
      target_difficulty: boundedInteger(values.get("target_difficulty"), 1, 5),
      weekly_minutes: boundedInteger(values.get("weekly_minutes"), 15, 10_080),
      adaptive_difficulty: values.get("adaptive_difficulty") === "on",
    }, signal));
    if (value !== undefined) {
      setPrograms((current) => replaceProgram(current, value));
      setSelectedId(value.id);
      form.reset();
    }
  }

  return <aside className="workflow-panel learning-panel" aria-labelledby="learning-panel-title">
    <header className="workflow-panel-header">
      <div><p className="eyebrow">Universal learning</p><h2 id="learning-panel-title">AI Teacher</h2></div>
      <button type="button" className="button button-quiet" onClick={onClose}>Close</button>
    </header>
    <p className="field-help">Curricula, generated lessons, exact-answer practice, adaptive difficulty, progress, and spaced repetition stay private to this owner. Pronunciation scoring is unavailable until a verified provider is configured.</p>
    {capabilities !== null && <p className="notice">Teacher, speaking partner, exam, vocabulary, and spaced-repetition modes: ready. Pronunciation: {capabilities.pronunciation_status}.</p>}
    {notice !== null && <p className="notice notice-error" role="alert">{notice}</p>}
    <details open={programs.length === 0}>
      <summary>Create beginner-to-advanced curriculum</summary>
      <form className="workflow-form" onSubmit={(event) => void createProgram(event)}>
        <label>Subject<input name="subject" required maxLength={160} /></label>
        <label>Goal<textarea name="goal" required maxLength={2_000} /></label>
        <label>Target/content language<input name="target_language" required defaultValue="ja" pattern="[A-Za-z][A-Za-z0-9-]{1,34}" /></label>
        <label>Teaching language<input name="instruction_language" required defaultValue="en" pattern="[A-Za-z][A-Za-z0-9-]{1,34}" /></label>
        <label>Starting difficulty<input name="start_difficulty" type="number" min="1" max="5" defaultValue="1" required /></label>
        <label>Target difficulty<input name="target_difficulty" type="number" min="1" max="5" defaultValue="5" required /></label>
        <label>Weekly minutes<input name="weekly_minutes" type="number" min="15" max="10080" defaultValue="150" required /></label>
        <label><input name="adaptive_difficulty" type="checkbox" defaultChecked /> Adapt difficulty from verified attempts</label>
        <button disabled={busy} className="button button-primary">Create curriculum</button>
      </form>
    </details>
    {loading ? <p role="status">Loading learning programs…</p> : programs.length > 0 && <label>Active program<select value={selected?.id ?? ""} onChange={(event) => setSelectedId(event.target.value)}>{programs.map((program) => <option key={program.id} value={program.id}>{program.subject}</option>)}</select></label>}
    {selected !== null && <>
      <section className="finance-summary" aria-label="Learning progress">
        <strong>{selected.subject}</strong><span>{selected.completed_lessons}/{selected.total_lessons} lessons</span><span>{selected.progress_bps / 100}%</span><span>Difficulty {selected.current_difficulty}/5</span>
      </section>
      <ol className="learning-curriculum">{selected.lessons.map((lesson) => <li key={lesson.id}>
        <header><strong>{lesson.position}. {lesson.title}</strong><span>{lesson.status} · level {lesson.difficulty}/5</span></header>
        <ul>{lesson.objectives.map((objective) => <li key={objective}>{objective}</li>)}</ul>
        {lesson.status === "planned" && <button type="button" className="button button-primary" disabled={busy} onClick={() => void perform((signal) => onGenerateLesson(selected.id, lesson.id, signal)).then((value) => { if (value !== undefined) setPrograms((current) => replaceProgram(current, value)); })}>Generate verified lesson</button>}
        {lesson.content !== null && <details><summary>Lesson content</summary><div className="learning-content">{lesson.content}</div><p className="field-help">Verified artifact {lesson.output_sha256?.slice(0, 12)} · model {lesson.model_id} · memory preferences {lesson.memory_context_count}</p></details>}
        {lesson.status !== "planned" && <details><summary>Add exercise / quiz / conversation / revision</summary>
          <form className="workflow-form" onSubmit={(event) => {
            event.preventDefault(); const form = event.currentTarget; const values = new FormData(form);
            void perform((signal) => onCreateActivity(selected.id, lesson.id, {
              kind: String(values.get("kind")) as LearningActivityCreateRequest["kind"],
              prompt: String(values.get("prompt") ?? "").trim(),
              expected_answer: String(values.get("expected_answer") ?? "").trim(),
              explanation: String(values.get("explanation") ?? "").trim(),
              difficulty: boundedInteger(values.get("difficulty"), 1, 5),
              max_attempts: boundedInteger(values.get("max_attempts"), 1, 10),
            }, signal)).then((value) => { if (value !== undefined) { setPrograms((current) => replaceProgram(current, value)); form.reset(); } });
          }}><label>Mode<select name="kind"><option value="exercise">Exercise</option><option value="quiz">Exam/quiz</option><option value="conversation">Speaking partner text turn</option><option value="revision">Revision</option></select></label><label>Prompt<textarea name="prompt" required /></label><label>Exact answer key<input name="expected_answer" required /></label><label>Explanation<textarea name="explanation" required /></label><label>Difficulty<input name="difficulty" type="number" min="1" max="5" defaultValue={lesson.difficulty} required /></label><label>Attempts<input name="max_attempts" type="number" min="1" max="10" defaultValue="3" required /></label><button disabled={busy} className="button button-primary">Add practice</button></form>
        </details>}
        {lesson.activities.map((activity) => <form className="workflow-form learning-attempt" key={activity.id} onSubmit={(event) => {
          event.preventDefault(); const form = event.currentTarget; const values = new FormData(form);
          void perform((signal) => onAttempt(selected.id, activity.id, { answer: String(values.get("answer") ?? "").trim() }, signal)).then((attempt) => { if (attempt !== undefined) { setNotice(attempt.feedback); void refresh(selected.id); form.reset(); } });
        }}><strong>{activity.kind}: {activity.prompt}</strong><label>Your answer<input name="answer" required /></label><button disabled={busy || activity.attempts.length >= activity.max_attempts} className="button button-primary">Check answer</button>{activity.attempts.length > 0 && <span>{activity.attempts.at(-1)?.is_correct ? "Correct" : "Try again"} · {activity.attempts.length}/{activity.max_attempts}</span>}</form>)}
      </li>)}</ol>
      <details><summary>Vocabulary trainer and spaced repetition ({selected.review_items.length})</summary>
        <form className="workflow-form" onSubmit={(event) => {
          event.preventDefault(); const form = event.currentTarget; const values = new FormData(form);
          void perform((signal) => onCreateReviewItem(selected.id, { front: String(values.get("front") ?? "").trim(), back: String(values.get("back") ?? "").trim() }, signal)).then((value) => { if (value !== undefined) { void refresh(selected.id); form.reset(); } });
        }}><label>Front / prompt<input name="front" required /></label><label>Back / answer<textarea name="back" required /></label><button disabled={busy} className="button button-primary">Add review card</button></form>
        <ul>{selected.review_items.map((item) => <li key={item.id}><strong>{item.front}</strong> — {item.back} · due {new Date(item.due_at).toLocaleDateString()} <label>Recall quality<select defaultValue="4" onChange={(event) => { const quality = Number(event.target.value); void perform((signal) => onReview(selected.id, item.id, { quality }, signal)).then((value) => { if (value !== undefined) void refresh(selected.id); }); }}><option value="0">0 — forgot</option><option value="1">1</option><option value="2">2</option><option value="3">3</option><option value="4">4</option><option value="5">5 — effortless</option></select></label></li>)}</ul>
      </details>
      <section id="pronunciation-scoring" className="notice"><strong>Pronunciation scoring</strong><p>External dependency: a verified scoring provider and microphone permission are required. No score is fabricated locally.</p></section>
    </>}
  </aside>;
}
