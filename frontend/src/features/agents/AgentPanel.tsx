import { useCallback, useEffect, useRef, useState } from "react";
import { presenceStateForAgentStatus, type PresenceState } from "@work-station/shared";

import type {
  AgentKind,
  AgentOSCapabilities,
  AgentRun,
  AgentRunCreateRequest,
  AgentRunEvent,
  ModelTask,
} from "../../api/contracts";


interface AgentPanelProps {
  onClose: () => void;
  onLoadCapabilities: (signal?: AbortSignal) => Promise<AgentOSCapabilities>;
  onLoadRuns: (signal?: AbortSignal) => Promise<AgentRun[]>;
  onCreate: (request: AgentRunCreateRequest) => Promise<AgentRun>;
  onCancel: (runId: string) => Promise<AgentRun>;
  onControl: (
    action: "pause" | "resume" | "approve" | "retry",
    runId: string,
  ) => Promise<AgentRun>;
  onModify: (runId: string, goal: string) => Promise<AgentRun>;
  onStreamEvents?: (
    runId: string,
    onEvent: (event: AgentRunEvent) => void,
    signal: AbortSignal,
    after?: number,
  ) => Promise<void>;
  onPresenceStateChange?: (state: PresenceState | null) => void;
}

const TASKS: Array<{ value: ModelTask; label: string }> = [
  { value: "general_chat", label: "General" },
  { value: "reasoning", label: "Reasoning" },
  { value: "mathematics", label: "Mathematics" },
  { value: "coding", label: "Coding" },
  { value: "code_generation", label: "Code generation" },
  { value: "debugging", label: "Debugging" },
  { value: "expert_analysis", label: "Research / expert analysis" },
  { value: "vision", label: "Vision" },
  { value: "rag", label: "RAG / knowledge" },
  { value: "workflow_planning", label: "Workflow planning" },
  { value: "long_context", label: "Long context" },
  { value: "exact_output", label: "Exact output" },
];

const TERMINAL = new Set(["completed", "failed", "cancelled", "timed_out"]);
const STREAMING = new Set(["queued", "planning", "running", "verifying", "retrying"]);

export function AgentPanel({
  onClose,
  onLoadCapabilities,
  onLoadRuns,
  onCreate,
  onCancel,
  onControl,
  onModify,
  onStreamEvents,
  onPresenceStateChange,
}: AgentPanelProps) {
  const [capabilities, setCapabilities] = useState<AgentOSCapabilities | null>(null);
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [goal, setGoal] = useState("");
  const [task, setTask] = useState<ModelTask>("general_chat");
  const [specialist, setSpecialist] = useState<AgentKind | "auto">("auto");
  const [requireApproval, setRequireApproval] = useState(false);
  const [revisions, setRevisions] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    try {
      const [profileSnapshot, runSnapshot] = await Promise.all([
        onLoadCapabilities(current.signal),
        onLoadRuns(current.signal),
      ]);
      if (!current.signal.aborted) {
        setCapabilities(profileSnapshot);
        setRuns(runSnapshot);
        setNotice(null);
      }
    } catch {
      if (!current.signal.aborted) setNotice("Agent status could not be loaded.");
    }
  }, [onLoadCapabilities, onLoadRuns]);

  useEffect(() => {
    void load();
    return () => controller.current?.abort();
  }, [load]);

  useEffect(() => {
    if (onStreamEvents !== undefined || !runs.some((run) => STREAMING.has(run.status))) return;
    const timer = window.setInterval(() => void load(), 1500);
    return () => window.clearInterval(timer);
  }, [load, onStreamEvents, runs]);

  const activeRun = runs.find((run) => STREAMING.has(run.status));
  const activeRunId = activeRun?.id;
  useEffect(() => {
    if (activeRunId === undefined || onStreamEvents === undefined) return;
    const stream = new AbortController();
    void onStreamEvents(
      activeRunId,
      (event) => {
        setRuns((current) => current.map((run) => {
          if (run.id !== activeRunId) return run;
          const events = run.events.some((item) => item.sequence === event.sequence)
            ? run.events
            : [...run.events, event];
          return {
            ...run,
            status: event.status,
            updated_at: event.created_at,
            events,
          };
        }));
        void load();
      },
      stream.signal,
      0,
    ).catch(() => {
      if (!stream.signal.aborted) setNotice("Live mission activity disconnected; reconnecting requires Refresh.");
    });
    return () => stream.abort();
  }, [activeRunId, load, onStreamEvents]);

  useEffect(() => {
    const latest = runs.find((run) => !TERMINAL.has(run.status)) ?? runs[0];
    onPresenceStateChange?.(presenceStateForAgentStatus(latest?.status));
  }, [onPresenceStateChange, runs]);

  useEffect(
    () => () => onPresenceStateChange?.(null),
    [onPresenceStateChange],
  );

  const submit = useCallback(async () => {
    const normalized = goal.trim();
    if (!normalized || normalized !== goal || normalized.length > 32_000) {
      setNotice("Enter an exact nonblank goal without surrounding whitespace.");
      return;
    }
    setBusy(true);
    setNotice(null);
    try {
      const created = await onCreate({
        goal,
        task,
        ...(specialist === "auto" ? {} : { specialist }),
        max_retries: 1,
        deadline_seconds: 180,
        source: "text",
        require_owner_approval: requireApproval,
      });
      setRuns((current) => [created, ...current.filter((run) => run.id !== created.id)]);
      setGoal("");
      setNotice("Agent run submitted with model-inference permission only.");
    } catch {
      setNotice("The bounded agent run could not be submitted.");
    } finally {
      setBusy(false);
    }
  }, [goal, onCreate, requireApproval, specialist, task]);

  const updateRun = useCallback((updated: AgentRun) => {
    setRuns((current) => current.map((item) => (
      item.id === updated.id ? updated : item
    )));
  }, []);

  const control = useCallback(async (
    action: "pause" | "resume" | "approve" | "retry",
    runId: string,
  ) => {
    setBusy(true);
    setNotice(null);
    try {
      updateRun(await onControl(action, runId));
    } catch {
      setNotice(`The mission could not be ${action === "retry" ? "retried" : `${action}d`}.`);
    } finally {
      setBusy(false);
    }
  }, [onControl, updateRun]);

  const modify = useCallback(async (run: AgentRun) => {
    const revisionGoal = revisions[run.id] ?? "";
    if (!revisionGoal.trim() || revisionGoal !== revisionGoal.trim()) {
      setNotice("Enter an exact nonblank revised goal without surrounding whitespace.");
      return;
    }
    setBusy(true);
    setNotice(null);
    try {
      updateRun(await onModify(run.id, revisionGoal));
      setRevisions((current) => ({ ...current, [run.id]: "" }));
    } catch {
      setNotice("The mission revision could not be applied.");
    } finally {
      setBusy(false);
    }
  }, [onModify, revisions, updateRun]);

  return (
    <aside className="settings-panel" aria-labelledby="agents-title">
      <div className="panel-header">
        <div>
          <p className="eyebrow">AGENT OS</p>
          <h2 id="agents-title">Specialist agents</h2>
          <p>
            Goals follow a bounded plan → route → execute → independently verify → retry lifecycle.
          </p>
        </div>
        <label>
          <input
            type="checkbox"
            checked={requireApproval}
            onChange={(event) => setRequireApproval(event.target.checked)}
          />
          <span>Hold for my approval before execution</span>
        </label>
        <button type="button" className="button button-quiet" onClick={onClose}>Close</button>
      </div>

      <section className="settings-section" aria-labelledby="new-agent-title">
        <h3 id="new-agent-title">New agent run</h3>
        <label>
          <span>Goal</span>
          <textarea
            aria-label="Agent goal"
            value={goal}
            maxLength={32_000}
            onChange={(event) => setGoal(event.target.value)}
          />
        </label>
        <div className="session-controls">
          <label>
            <span>Task</span>
            <select value={task} onChange={(event) => setTask(event.target.value as ModelTask)}>
              {TASKS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </label>
          <label>
            <span>Specialist</span>
            <select value={specialist} onChange={(event) => setSpecialist(event.target.value as AgentKind | "auto")}>
              <option value="auto">Automatic</option>
              {(capabilities?.profiles ?? []).filter((profile) => profile.registered).map((profile) => (
                <option key={profile.kind} value={profile.kind}>{profile.kind}</option>
              ))}
            </select>
          </label>
        </div>
        <button type="button" className="button button-primary" disabled={busy} onClick={() => void submit()}>
          Run agent
        </button>
        <p className="field-help">
          {capabilities === null
            ? "Loading typed agent profiles…"
            : `${capabilities.active_runs} active · ${capabilities.max_concurrency} maximum concurrent · ${capabilities.persistence.replaceAll("_", " ")}`}
        </p>
        {notice !== null && <p role="status">{notice}</p>}
      </section>

      <section className="settings-section" aria-labelledby="agent-runs-title">
        <div className="settings-section-heading">
          <h3 id="agent-runs-title">Owner agent runs</h3>
          <button type="button" className="button button-quiet" onClick={() => void load()}>Refresh</button>
        </div>
        {runs.length === 0 && <p className="field-help">No agent runs retained.</p>}
        <ul className="capability-list" aria-label="Agent runs">
          {runs.map((run) => (
            <li key={run.id}>
              <div className="capability-heading">
                <strong>{(run.specialist ?? run.task).replaceAll("_", " ")}</strong>
                <span className={`capability-status capability-status-${run.status === "completed" ? "available" : run.status}`}>
                  {run.status.replaceAll("_", " ")}
                </span>
              </div>
              <p><strong>Mission:</strong> {run.goal}</p>
              <p className="field-help">Input: {run.source}</p>
              <div className="mission-contract-grid">
                <section aria-label="Mission plan">
                  <strong>Plan</strong>
                  {run.plan.length === 0 ? (
                    <p className="field-help">Planning has not produced a typed step yet.</p>
                  ) : (
                    <ol>
                      {run.plan.map((step) => (
                        <li key={step.step_id}>
                          {step.step_id.replaceAll("-", " ")} · {step.agent} · {step.task.replaceAll("_", " ")}
                        </li>
                      ))}
                    </ol>
                  )}
                </section>
                <section aria-label="Mission tools and permissions">
                  <strong>Tools & permissions</strong>
                  <p className="field-help">
                    {run.plan.length === 0
                      ? "Awaiting the typed plan."
                      : run.plan.flatMap((step) => step.permissions).join(", ")}
                  </p>
                  <p className="field-help">No tool execution is delegated by this model-inference-only mission.</p>
                </section>
              </div>
              <section aria-label="Live mission activity">
                <strong>Live activity</strong>
                <ol className="mission-activity">
                  {run.events.map((event) => (
                    <li key={event.sequence}>
                      {event.action.replaceAll("_", " ")} · {event.status.replaceAll("_", " ")}
                      {event.agent === null ? "" : ` · ${event.agent}`}
                      {event.attempt === null ? "" : ` · attempt ${event.attempt}`}
                    </li>
                  ))}
                </ol>
              </section>
              {run.output !== null && <p className="message-body">{run.output}</p>}
              {run.failure_code !== null && <p className="notice notice-error">{run.failure_code.replaceAll("_", " ")}</p>}
              {run.attempts.map((attempt) => (
                <p className="field-help" key={`${attempt.step_id}-${attempt.attempt}`}>
                  Attempt {attempt.attempt} · {attempt.agent} · {attempt.verified ? "verified" : "verification failed"}
                </p>
              ))}
              <p className="field-help">
                Revision {run.revision} · manual retries {run.manual_retry_count}/3
              </p>
              {run.can_modify && (
                <label>
                  <span>Revised mission goal</span>
                  <textarea
                    aria-label={`Revised mission goal for ${run.id}`}
                    maxLength={32_000}
                    value={revisions[run.id] ?? ""}
                    onChange={(event) => setRevisions((current) => ({
                      ...current,
                      [run.id]: event.target.value,
                    }))}
                  />
                </label>
              )}
              <div className="session-controls" aria-label="Mission controls">
                {run.can_pause && (
                  <button type="button" className="button button-quiet" disabled={busy} onClick={() => void control("pause", run.id)}>Pause</button>
                )}
                {run.can_resume && (
                  <button type="button" className="button button-quiet" disabled={busy} onClick={() => void control("resume", run.id)}>Resume</button>
                )}
                {run.can_approve && (
                  <button type="button" className="button button-primary" disabled={busy} onClick={() => void control("approve", run.id)}>Approve</button>
                )}
                {run.can_modify && (
                  <button type="button" className="button button-quiet" disabled={busy} onClick={() => void modify(run)}>Apply revision</button>
                )}
                {run.can_retry && (
                  <button type="button" className="button button-quiet" disabled={busy} onClick={() => void control("retry", run.id)}>Retry</button>
                )}
              </div>
              {!TERMINAL.has(run.status) && (
                <button
                  type="button"
                  className="button button-quiet"
                  disabled={busy}
                  onClick={() => {
                    setBusy(true);
                    void onCancel(run.id)
                      .then(updateRun)
                      .catch(() => setNotice("The agent run could not be cancelled."))
                      .finally(() => setBusy(false));
                  }}
                >
                  Cancel run
                </button>
              )}
            </li>
          ))}
        </ul>
      </section>
    </aside>
  );
}
