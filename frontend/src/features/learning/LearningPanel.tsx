import { type FormEvent, useEffect, useMemo, useState } from "react";

import type {
  LearningActivityCreateRequest,
  LearningAnalytics,
  LearningAttempt,
  LearningAttemptRequest,
  LearningCapabilities,
  LearningEventPage,
  LearningHint,
  LearningProfileUpdateRequest,
  LearningProgram,
  LearningProgramCreateRequest,
  LearningReviewItem,
  LearningReviewItemCreateRequest,
  LearningReviewRequest,
  LearningSession,
  LearningSessionCreateRequest,
  LearningStudyPlan,
} from "../../api/contracts";


interface LearningPanelProps {
  onClose: () => void;
  onCapabilities: (signal?: AbortSignal) => Promise<LearningCapabilities>;
  onLoad: (signal?: AbortSignal) => Promise<LearningProgram[]>;
  onGet: (id: string, signal?: AbortSignal) => Promise<LearningProgram>;
  onCreate: (request: LearningProgramCreateRequest, signal?: AbortSignal) => Promise<LearningProgram>;
  onGenerateLesson: (programId: string, lessonId: string, signal?: AbortSignal) => Promise<LearningProgram>;
  onGenerateAssessment: (programId: string, lessonId: string, signal?: AbortSignal) => Promise<LearningProgram>;
  onCreateActivity: (programId: string, lessonId: string, request: LearningActivityCreateRequest, signal?: AbortSignal) => Promise<LearningProgram>;
  onAttempt: (programId: string, activityId: string, request: LearningAttemptRequest, signal?: AbortSignal) => Promise<LearningAttempt>;
  onHint: (programId: string, activityId: string, signal?: AbortSignal) => Promise<LearningHint>;
  onCreateReviewItem: (programId: string, request: LearningReviewItemCreateRequest, signal?: AbortSignal) => Promise<LearningReviewItem>;
  onReview: (programId: string, itemId: string, request: LearningReviewRequest, signal?: AbortSignal) => Promise<LearningReviewItem>;
  onUpdateProfile: (programId: string, request: LearningProfileUpdateRequest, signal?: AbortSignal) => Promise<LearningProgram>;
  onAttachSource: (programId: string, documentId: string, signal?: AbortSignal) => Promise<LearningProgram>;
  onDetachSource: (programId: string, sourceId: string, signal?: AbortSignal) => Promise<LearningProgram>;
  onStartSession: (programId: string, request: LearningSessionCreateRequest, signal?: AbortSignal) => Promise<LearningSession>;
  onTransitionSession: (programId: string, sessionId: string, action: "pause" | "resume" | "complete", signal?: AbortSignal) => Promise<LearningSession>;
  onAnalytics: (programId: string, signal?: AbortSignal) => Promise<LearningAnalytics>;
  onStudyPlan: (programId: string, signal?: AbortSignal) => Promise<LearningStudyPlan>;
  onAudit: (programId: string, signal?: AbortSignal) => Promise<LearningEventPage>;
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
  onGenerateAssessment, onCreateActivity, onAttempt, onHint, onCreateReviewItem,
  onReview, onUpdateProfile, onAttachSource, onDetachSource, onStartSession,
  onTransitionSession, onAnalytics, onStudyPlan, onAudit,
}: LearningPanelProps) {
  const [programs, setPrograms] = useState<LearningProgram[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [capabilities, setCapabilities] = useState<LearningCapabilities | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [analytics, setAnalytics] = useState<LearningAnalytics | null>(null);
  const [studyPlan, setStudyPlan] = useState<LearningStudyPlan | null>(null);
  const [audit, setAudit] = useState<LearningEventPage | null>(null);
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

  useEffect(() => {
    if (selected === null) {
      setAnalytics(null); setStudyPlan(null); setAudit(null); return;
    }
    const controller = new AbortController();
    void Promise.all([
      onAnalytics(selected.id, controller.signal),
      onStudyPlan(selected.id, controller.signal),
      onAudit(selected.id, controller.signal),
    ]).then(([nextAnalytics, nextPlan, nextAudit]) => {
      if (controller.signal.aborted) return;
      setAnalytics(nextAnalytics); setStudyPlan(nextPlan); setAudit(nextAudit);
    }).catch(() => {
      if (!controller.signal.aborted) setNotice("Learning insights could not be loaded.");
    });
    return () => controller.abort();
  }, [onAnalytics, onAudit, onStudyPlan, selected]);

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

  async function refreshInsights(programId: string, signal: AbortSignal) {
    const [nextAnalytics, nextPlan, nextAudit] = await Promise.all([
      onAnalytics(programId, signal), onStudyPlan(programId, signal), onAudit(programId, signal),
    ]);
    setAnalytics(nextAnalytics); setStudyPlan(nextPlan); setAudit(nextAudit);
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
      teaching_mode: String(values.get("teaching_mode")) as LearningProgramCreateRequest["teaching_mode"],
      preferences: {
        explanation_style: "step_by_step",
        hints_before_answers: true,
        mixed_language: values.get("mixed_language") === "on",
        preferred_session_minutes: 30,
        pace: "balanced",
      },
      source_document_ids: [],
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
    <p className="field-help">Private curricula, resumable sessions, generated assessments, rubric grading, mastery analytics, source-grounded teaching, and spaced repetition. Pronunciation scoring remains an explicit external boundary.</p>
    {capabilities !== null && <p className="notice">Teacher, Socratic, coaching, interview, pair-programming, mixed-language, grounding, and audit modes: ready. Pronunciation: {capabilities.pronunciation_status}.</p>}
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
        <label>Teaching mode<select name="teaching_mode" defaultValue="teacher"><option value="teacher">Teacher</option><option value="socratic">Socratic</option><option value="coach">Coach</option><option value="mentor">Mentor</option><option value="interviewer">Interviewer</option><option value="pair_programming">Pair programming</option><option value="study">Study</option><option value="focus">Focus</option><option value="exam">Exam</option><option value="revision">Revision</option></select></label>
        <label><input name="adaptive_difficulty" type="checkbox" defaultChecked /> Adapt difficulty from verified attempts</label>
        <label><input name="mixed_language" type="checkbox" /> Allow mixed-language / Hinglish explanations</label>
        <button disabled={busy} className="button button-primary">Create curriculum</button>
      </form>
    </details>
    {loading ? <p role="status">Loading learning programs…</p> : programs.length > 0 && <label>Active program<select value={selected?.id ?? ""} onChange={(event) => setSelectedId(event.target.value)}>{programs.map((program) => <option key={program.id} value={program.id}>{program.subject}</option>)}</select></label>}
    {selected !== null && <>
      <section className="finance-summary" aria-label="Learning progress">
        <strong>{selected.subject}</strong><span>{selected.completed_lessons}/{selected.total_lessons} lessons</span><span>{selected.progress_bps / 100}%</span><span>Difficulty {selected.current_difficulty}/5</span><span>{selected.teaching_mode}</span>
      </section>
      {analytics !== null && <section className="finance-summary" aria-label="Learning analytics">
        <span>Mastery {analytics.mastery_bps === null ? "not assessed" : `${analytics.mastery_bps / 100}%`}</span><span>Confidence {analytics.confidence_bps / 100}%</span><span>Due {analytics.due_review_count}</span><span>Streak {analytics.current_streak_days} day(s)</span>
      </section>}
      <details open={analytics?.active_session !== null}><summary>Study session and teaching profile</summary>
        {analytics?.active_session === null || analytics === null ? <form className="workflow-form" onSubmit={(event) => {
          event.preventDefault(); const form = event.currentTarget; const values = new FormData(form);
          void perform(async (signal) => { await onStartSession(selected.id, { mode: String(values.get("mode")) as LearningSessionCreateRequest["mode"], focus: String(values.get("focus") ?? "").trim(), planned_minutes: boundedInteger(values.get("minutes"), 5, 480), current_lesson_id: null }, signal); await refreshInsights(selected.id, signal); });
        }}><label>Mode<select name="mode" defaultValue={selected.teaching_mode}><option value="teacher">Teacher</option><option value="socratic">Socratic</option><option value="coach">Coach</option><option value="mentor">Mentor</option><option value="interviewer">Interviewer</option><option value="pair_programming">Pair programming</option><option value="study">Study</option><option value="focus">Focus</option><option value="exam">Exam</option><option value="revision">Revision</option></select></label><label>Focus<input name="focus" required maxLength={500} defaultValue={selected.subject} /></label><label>Minutes<input name="minutes" type="number" min="5" max="480" defaultValue={selected.preferences.preferred_session_minutes} required /></label><button disabled={busy} className="button button-primary">Start real session</button></form> : <div className="workflow-form"><strong>{analytics.active_session.mode} · {analytics.active_session.status}</strong><span>{analytics.active_session.focus} · {analytics.active_session.planned_minutes} minutes · {analytics.active_session.interruption_count} interruption(s)</span><div className="button-row">{analytics.active_session.status === "active" ? <button type="button" disabled={busy} className="button" onClick={() => void perform(async (signal) => { await onTransitionSession(selected.id, analytics.active_session!.id, "pause", signal); await refreshInsights(selected.id, signal); })}>Pause</button> : <button type="button" disabled={busy} className="button" onClick={() => void perform(async (signal) => { await onTransitionSession(selected.id, analytics.active_session!.id, "resume", signal); await refreshInsights(selected.id, signal); })}>Resume</button>}<button type="button" disabled={busy} className="button button-primary" onClick={() => void perform(async (signal) => { await onTransitionSession(selected.id, analytics.active_session!.id, "complete", signal); await refreshInsights(selected.id, signal); })}>Complete session</button></div></div>}
        <form className="workflow-form" onSubmit={(event) => { event.preventDefault(); const form = event.currentTarget; const values = new FormData(form); void perform((signal) => onUpdateProfile(selected.id, { teaching_mode: String(values.get("mode")) as LearningProfileUpdateRequest["teaching_mode"], preferences: { ...selected.preferences, explanation_style: String(values.get("style")) as LearningProfileUpdateRequest["preferences"]["explanation_style"], mixed_language: values.get("mixed") === "on" } }, signal)).then((value) => { if (value !== undefined) setPrograms((current) => replaceProgram(current, value)); }); }}><label>Default mode<select name="mode" defaultValue={selected.teaching_mode}><option value="teacher">Teacher</option><option value="socratic">Socratic</option><option value="coach">Coach</option><option value="mentor">Mentor</option><option value="interviewer">Interviewer</option><option value="pair_programming">Pair programming</option><option value="study">Study</option><option value="focus">Focus</option><option value="exam">Exam</option><option value="revision">Revision</option></select></label><label>Explanation style<select name="style" defaultValue={selected.preferences.explanation_style}><option value="concise">Concise</option><option value="detailed">Detailed</option><option value="step_by_step">Step by step</option><option value="example_first">Example first</option></select></label><label><input name="mixed" type="checkbox" defaultChecked={selected.preferences.mixed_language} /> Mixed-language teaching</label><button disabled={busy} className="button">Save teaching profile</button></form>
      </details>
      <details><summary>Knowledge sources ({selected.sources.length})</summary><p className="field-help">Attach an already ingested private document by ID. Generated lessons retrieve only owner-scoped, unchanged source chunks and require preserved citations.</p><form className="workflow-form" onSubmit={(event) => { event.preventDefault(); const form = event.currentTarget; const documentId = String(new FormData(form).get("document_id") ?? "").trim(); void perform((signal) => onAttachSource(selected.id, documentId, signal)).then((value) => { if (value !== undefined) { setPrograms((current) => replaceProgram(current, value)); form.reset(); } }); }}><label>Document ID<input name="document_id" required pattern="[0-9a-fA-F-]{36}" /></label><button disabled={busy} className="button button-primary">Attach source</button></form><ul>{selected.sources.map((source) => <li key={source.id}><strong>{source.label}</strong> · integrity {source.source_sha256.slice(0, 12)} <button type="button" disabled={busy} className="button button-quiet" onClick={() => void perform((signal) => onDetachSource(selected.id, source.id, signal)).then((value) => { if (value !== undefined) setPrograms((current) => replaceProgram(current, value)); })}>Detach</button></li>)}</ul></details>
      {studyPlan !== null && <details><summary>Personal daily / weekly plan</summary><ol>{studyPlan.items.map((item) => <li key={item.date}><strong>{item.date}</strong> · {item.minutes} min · {item.mode} · {item.focus}</li>)}</ol></details>}
      {analytics !== null && <details><summary>Mastery and weak-topic analysis ({analytics.skills.length})</summary>{analytics.weak_topics.length > 0 ? <p>Revise: {analytics.weak_topics.join(", ")}</p> : <p>No weak topic has enough evidence yet.</p>}<ul>{analytics.skills.map((skill) => <li key={skill.id}>{skill.name}: mastery {skill.mastery_bps / 100}% · confidence {skill.confidence_bps / 100}% · {skill.mistake_count}/{skill.attempts} mistakes</li>)}</ul></details>}
      <ol className="learning-curriculum">{selected.lessons.map((lesson) => <li key={lesson.id}>
        <header><strong>{lesson.position}. {lesson.title}</strong><span>{lesson.status} · level {lesson.difficulty}/5</span></header>
        <ul>{lesson.objectives.map((objective) => <li key={objective}>{objective}</li>)}</ul>
        {lesson.status === "planned" && <button type="button" className="button button-primary" disabled={busy} onClick={() => void perform((signal) => onGenerateLesson(selected.id, lesson.id, signal)).then((value) => { if (value !== undefined) setPrograms((current) => replaceProgram(current, value)); })}>Generate verified lesson</button>}
        {lesson.content !== null && <details><summary>Lesson content</summary><div className="learning-content">{lesson.content}</div><p className="field-help">Verified artifact {lesson.output_sha256?.slice(0, 12)} · model {lesson.model_id} · memory preferences {lesson.memory_context_count} · {lesson.grounding_state} ({lesson.source_context_count} source(s))</p><button type="button" className="button" disabled={busy || lesson.activities.some((activity) => activity.generated)} onClick={() => void perform((signal) => onGenerateAssessment(selected.id, lesson.id, signal)).then((value) => { if (value !== undefined) setPrograms((current) => replaceProgram(current, value)); })}>Generate verified assessment</button></details>}
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
              skill_name: String(values.get("skill_name") ?? "General").trim(), grading_mode: "exact", hints: String(values.get("hints") ?? "").split("|").map((item) => item.trim()).filter(Boolean), rubric_keywords: [], source_ids: [], required: true,
            }, signal)).then((value) => { if (value !== undefined) { setPrograms((current) => replaceProgram(current, value)); form.reset(); } });
          }}><label>Mode<select name="kind"><option value="exercise">Exercise</option><option value="quiz">Exam/quiz</option><option value="conversation">Speaking partner text turn</option><option value="revision">Revision</option><option value="coding">Coding tutor</option><option value="assignment">Assignment</option></select></label><label>Skill / topic<input name="skill_name" required defaultValue={selected.subject} /></label><label>Prompt<textarea name="prompt" required /></label><label>Exact answer key<input name="expected_answer" required /></label><label>Explanation<textarea name="explanation" required /></label><label>Progressive hints (separate with |)<input name="hints" /></label><label>Difficulty<input name="difficulty" type="number" min="1" max="5" defaultValue={lesson.difficulty} required /></label><label>Attempts<input name="max_attempts" type="number" min="1" max="10" defaultValue="3" required /></label><button disabled={busy} className="button button-primary">Add practice</button></form>
        </details>}
        {lesson.activities.map((activity) => <form className="workflow-form learning-attempt" key={activity.id} onSubmit={(event) => {
          event.preventDefault(); const form = event.currentTarget; const values = new FormData(form);
          void perform((signal) => onAttempt(selected.id, activity.id, { answer: String(values.get("answer") ?? "").trim() }, signal)).then((attempt) => { if (attempt !== undefined) { setNotice(attempt.feedback); void refresh(selected.id); form.reset(); } });
        }}><strong>{activity.kind}: {activity.prompt}</strong><span>{activity.skill_name} · {activity.grading_mode} grading {activity.generated ? `· generated by ${activity.model_id}` : ""}</span><label>Your answer{activity.kind === "long_answer" || activity.kind === "assignment" ? <textarea name="answer" required /> : <input name="answer" required />}</label><div className="button-row"><button disabled={busy || activity.attempts.some((attempt) => attempt.is_correct) || activity.attempts.length >= activity.max_attempts} className="button button-primary">Check answer</button>{activity.hints_available > 0 && <button type="button" disabled={busy} className="button" onClick={() => void perform((signal) => onHint(selected.id, activity.id, signal)).then((hint) => { if (hint !== undefined) { setNotice(`Hint: ${hint.hint}`); void refresh(selected.id); } })}>Hint ({Math.max(0, activity.hints_available - activity.hints_requested)})</button>}</div>{activity.attempts.length > 0 && <span>{activity.attempts.at(-1)?.is_correct ? "Mastered" : `${(activity.attempts.at(-1)?.score_bps ?? 0) / 100}% · revise`} · {activity.attempts.length}/{activity.max_attempts}</span>}</form>)}
      </li>)}</ol>
      <details><summary>Vocabulary trainer and spaced repetition ({selected.review_items.length})</summary>
        <form className="workflow-form" onSubmit={(event) => {
          event.preventDefault(); const form = event.currentTarget; const values = new FormData(form);
          void perform((signal) => onCreateReviewItem(selected.id, { front: String(values.get("front") ?? "").trim(), back: String(values.get("back") ?? "").trim() }, signal)).then((value) => { if (value !== undefined) { void refresh(selected.id); form.reset(); } });
        }}><label>Front / prompt<input name="front" required /></label><label>Back / answer<textarea name="back" required /></label><button disabled={busy} className="button button-primary">Add review card</button></form>
        <ul>{selected.review_items.map((item) => <li key={item.id}><strong>{item.front}</strong> — {item.back} · due {new Date(item.due_at).toLocaleDateString()} <label>Recall quality<select defaultValue="4" onChange={(event) => { const quality = Number(event.target.value); void perform((signal) => onReview(selected.id, item.id, { quality }, signal)).then((value) => { if (value !== undefined) void refresh(selected.id); }); }}><option value="0">0 — forgot</option><option value="1">1</option><option value="2">2</option><option value="3">3</option><option value="4">4</option><option value="5">5 — effortless</option></select></label></li>)}</ul>
      </details>
      <section id="pronunciation-scoring" className="notice"><strong>Pronunciation scoring</strong><p>External dependency: a verified scoring provider and microphone permission are required. No score is fabricated locally.</p></section>
      {audit !== null && <details><summary>Learning audit ({audit.items.length})</summary><p className="field-help">State changes retain identifiers and metadata digests, never raw learner answers or secrets.</p><ol>{audit.items.slice(0, 20).map((event) => <li key={event.id}>{event.action} · {event.entity_kind} · {event.metadata_sha256.slice(0, 12)}</li>)}</ol></details>}
    </>}
  </aside>;
}
