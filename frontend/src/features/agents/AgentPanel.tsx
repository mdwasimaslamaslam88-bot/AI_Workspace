import { useCallback, useEffect, useRef, useState } from "react";
import { presenceStateForAgentStatus, type PresenceState } from "@work-station/shared";

import type {
  AgentKind,
  AgentOSCapabilities,
  AgentRun,
  AgentRunCreateRequest,
  ModelTask,
} from "../../api/contracts";


interface AgentPanelProps {
  onClose: () => void;
  onLoadCapabilities: (signal?: AbortSignal) => Promise<AgentOSCapabilities>;
  onLoadRuns: (signal?: AbortSignal) => Promise<AgentRun[]>;
  onCreate: (request: AgentRunCreateRequest) => Promise<AgentRun>;
  onCancel: (runId: string) => Promise<AgentRun>;
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

export function AgentPanel({
  onClose,
  onLoadCapabilities,
  onLoadRuns,
  onCreate,
  onCancel,
  onPresenceStateChange,
}: AgentPanelProps) {
  const [capabilities, setCapabilities] = useState<AgentOSCapabilities | null>(null);
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [goal, setGoal] = useState("");
  const [task, setTask] = useState<ModelTask>("general_chat");
  const [specialist, setSpecialist] = useState<AgentKind | "auto">("auto");
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
    if (!runs.some((run) => !TERMINAL.has(run.status))) return;
    const timer = window.setInterval(() => void load(), 1500);
    return () => window.clearInterval(timer);
  }, [load, runs]);

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
      });
      setRuns((current) => [created, ...current.filter((run) => run.id !== created.id)]);
      setGoal("");
      setNotice("Agent run submitted with model-inference permission only.");
    } catch {
      setNotice("The bounded agent run could not be submitted.");
    } finally {
      setBusy(false);
    }
  }, [goal, onCreate, specialist, task]);

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
              {run.output !== null && <p className="message-body">{run.output}</p>}
              {run.failure_code !== null && <p className="notice notice-error">{run.failure_code.replaceAll("_", " ")}</p>}
              {run.attempts.map((attempt) => (
                <p className="field-help" key={`${attempt.step_id}-${attempt.attempt}`}>
                  Attempt {attempt.attempt} · {attempt.agent} · {attempt.verified ? "verified" : "verification failed"}
                </p>
              ))}
              {!TERMINAL.has(run.status) && (
                <button
                  type="button"
                  className="button button-quiet"
                  disabled={busy}
                  onClick={() => {
                    setBusy(true);
                    void onCancel(run.id)
                      .then((cancelled) => setRuns((current) => current.map((item) => item.id === cancelled.id ? cancelled : item)))
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
