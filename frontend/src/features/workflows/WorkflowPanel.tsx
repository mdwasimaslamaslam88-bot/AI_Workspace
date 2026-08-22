import {
  type FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import type {
  Workflow,
  WorkflowCreateRequest,
  WorkflowStatus,
} from "../../api/contracts";

interface WorkflowPanelProps {
  onClose: () => void;
  onLoad: (signal?: AbortSignal) => Promise<Workflow[]>;
  onCreate: (
    request: WorkflowCreateRequest,
    signal?: AbortSignal,
  ) => Promise<Workflow>;
  onStart: (workflowId: string, signal?: AbortSignal) => Promise<Workflow>;
  onGet: (workflowId: string, signal?: AbortSignal) => Promise<Workflow>;
  onCancel: (workflowId: string, signal?: AbortSignal) => Promise<Workflow>;
}

const TERMINAL = new Set<WorkflowStatus>([
  "completed",
  "failed",
  "cancelled",
  "timed_out",
]);

const TOOL_LABELS: Record<string, string> = {
  document_search: "Document search",
  memory_search: "Memory search",
  conversation_search: "Conversation search",
};

function delay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("cancelled", "AbortError"));
      return;
    }
    let timeout = 0;
    const abort = () => {
      window.clearTimeout(timeout);
      reject(new DOMException("cancelled", "AbortError"));
    };
    timeout = window.setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, milliseconds);
    signal.addEventListener("abort", abort, { once: true });
  });
}

function formatTimestamp(timestamp: string): string {
  const parsed = new Date(timestamp);
  return Number.isNaN(parsed.valueOf()) ? "" : parsed.toLocaleString();
}

function replaceWorkflow(current: Workflow[], updated: Workflow): Workflow[] {
  return [updated, ...current.filter((item) => item.id !== updated.id)].sort(
    (left, right) => right.created_at.localeCompare(left.created_at),
  );
}

export function WorkflowPanel({
  onClose,
  onLoad,
  onCreate,
  onStart,
  onGet,
  onCancel,
}: WorkflowPanelProps) {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [name, setName] = useState("");
  const [query, setQuery] = useState("");
  const [documentSearch, setDocumentSearch] = useState(true);
  const [memorySearch, setMemorySearch] = useState(true);
  const [conversationSearch, setConversationSearch] = useState(true);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [activeIds, setActiveIds] = useState<Set<string>>(() => new Set());
  const [error, setError] = useState<string | null>(null);
  const controllers = useRef(new Set<AbortController>());

  const poll = useCallback(async (
    workflowId: string,
    controller: AbortController,
  ) => {
    try {
      for (let attempt = 0; attempt < 130; attempt += 1) {
        const workflow = await onGet(workflowId, controller.signal);
        if (controller.signal.aborted) return;
        setWorkflows((current) => replaceWorkflow(current, workflow));
        if (TERMINAL.has(workflow.status)) {
          setActiveIds((current) => {
            const next = new Set(current);
            next.delete(workflowId);
            return next;
          });
          return;
        }
        await delay(500, controller.signal);
      }
      if (!controller.signal.aborted) {
        setError("Workflow status could not be confirmed within its deadline.");
      }
    } catch {
      if (!controller.signal.aborted) {
        setError("Workflow progress could not be refreshed.");
      }
    } finally {
      controllers.current.delete(controller);
      if (!controller.signal.aborted) {
        setCreating(false);
        setActiveIds((current) => {
          const next = new Set(current);
          next.delete(workflowId);
          return next;
        });
      }
    }
  }, [onGet]);

  const beginPolling = useCallback((workflowId: string) => {
    const controller = new AbortController();
    controllers.current.add(controller);
    setActiveIds((current) => new Set(current).add(workflowId));
    void poll(workflowId, controller);
  }, [poll]);

  useEffect(() => {
    const activeControllers = controllers.current;
    const controller = new AbortController();
    activeControllers.add(controller);
    void onLoad(controller.signal)
      .then((items) => {
        if (controller.signal.aborted) return;
        setWorkflows(items);
        for (const running of items.filter((item) => item.status === "running")) {
          beginPolling(running.id);
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setError("Workflow history could not be loaded.");
        }
      })
      .finally(() => {
        activeControllers.delete(controller);
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => {
      for (const active of activeControllers) active.abort();
      activeControllers.clear();
    };
  }, [beginPolling, onLoad]);

  async function start(workflow: Workflow, controller: AbortController) {
    const scheduled = await onStart(workflow.id, controller.signal);
    if (controller.signal.aborted) return;
    setWorkflows((current) => replaceWorkflow(current, scheduled));
    controllers.current.delete(controller);
    beginPolling(workflow.id);
  }

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (
      !query.trim() || creating ||
      (!documentSearch && !memorySearch && !conversationSearch)
    ) return;
    const steps: WorkflowCreateRequest["steps"] = [];
    if (documentSearch) {
      steps.push({
        tool_name: "document_search",
        arguments: { query, limit: 4 },
      });
    }
    if (memorySearch) {
      steps.push({
        tool_name: "memory_search",
        arguments: { query, limit: 8 },
      });
    }
    if (conversationSearch) {
      steps.push({
        tool_name: "conversation_search",
        arguments: { query, limit: 10 },
      });
    }
    const controller = new AbortController();
    controllers.current.add(controller);
    setCreating(true);
    setError(null);
    try {
      const request: WorkflowCreateRequest = { steps };
      if (name.trim()) request.name = name;
      const created = await onCreate(request, controller.signal);
      if (controller.signal.aborted) return;
      setWorkflows((current) => replaceWorkflow(current, created));
      setName("");
      setQuery("");
      await start(created, controller);
    } catch {
      controllers.current.delete(controller);
      if (!controller.signal.aborted) {
        setCreating(false);
        setError("Workflow could not be created or started.");
      }
    }
  }

  async function startPending(workflow: Workflow) {
    if (activeIds.size !== 0) return;
    const controller = new AbortController();
    controllers.current.add(controller);
    setCreating(true);
    setError(null);
    try {
      await start(workflow, controller);
    } catch {
      controllers.current.delete(controller);
      if (!controller.signal.aborted) {
        setCreating(false);
        setError("Workflow could not be started.");
      }
    }
  }

  async function cancel(workflowId: string) {
    const controller = new AbortController();
    controllers.current.add(controller);
    setError(null);
    try {
      const workflow = await onCancel(workflowId, controller.signal);
      if (controller.signal.aborted) return;
      setWorkflows((current) => replaceWorkflow(current, workflow));
      if (TERMINAL.has(workflow.status)) {
        setActiveIds((current) => {
          const next = new Set(current);
          next.delete(workflowId);
          return next;
        });
      }
    } catch {
      if (!controller.signal.aborted) {
        setError("Workflow cancellation could not be confirmed.");
      }
    } finally {
      controllers.current.delete(controller);
    }
  }

  const hasSelection = documentSearch || memorySearch || conversationSearch;

  return (
    <aside className="workflow-panel" aria-labelledby="workflow-panel-title">
      <header className="workflow-panel-header">
        <div>
          <p className="eyebrow">Bounded agent workflows</p>
          <h2 id="workflow-panel-title">Research tasks</h2>
        </div>
        <button type="button" className="button button-quiet" onClick={onClose}>
          Close
        </button>
      </header>
      <p className="field-help">
        Compose up to three owner-scoped research tools. The server limits every
        step, the full 60-second task, permissions, output, and cancellation.
      </p>

      <form className="workflow-form" onSubmit={(event) => void create(event)}>
        <div>
          <label htmlFor="workflow-name">Task name <span>(optional)</span></label>
          <input
            id="workflow-name"
            value={name}
            maxLength={120}
            onChange={(event) => setName(event.target.value)}
            disabled={creating}
          />
        </div>
        <div>
          <label htmlFor="workflow-query">Research goal</label>
          <textarea
            id="workflow-query"
            value={query}
            maxLength={500}
            rows={3}
            placeholder="Find relevant context about…"
            onChange={(event) => setQuery(event.target.value)}
            disabled={creating}
          />
        </div>
        <fieldset disabled={creating}>
          <legend>Sources</legend>
          <label><input type="checkbox" checked={documentSearch} onChange={(event) => setDocumentSearch(event.target.checked)} /> Documents</label>
          <label><input type="checkbox" checked={memorySearch} onChange={(event) => setMemorySearch(event.target.checked)} /> Memory</label>
          <label><input type="checkbox" checked={conversationSearch} onChange={(event) => setConversationSearch(event.target.checked)} /> Conversations</label>
        </fieldset>
        <button
          className="button button-primary"
          disabled={creating || activeIds.size !== 0 || !query.trim() || !hasSelection}
        >
          {creating ? "Starting…" : "Run workflow"}
        </button>
      </form>

      {error !== null && (
        <p className="notice notice-error" role="alert">{error}</p>
      )}
      {activeIds.size !== 0 && (
        <p role="status">Workflow is running within fixed server bounds…</p>
      )}

      <h3>Task history</h3>
      {loading ? (
        <p className="muted" role="status">Loading workflows…</p>
      ) : (
        <ol className="workflow-history" aria-label="Workflow task history">
          {workflows.length === 0 && (
            <li className="muted">No workflows yet.</li>
          )}
          {workflows.map((workflow) => {
            const completedSteps = workflow.steps.filter(
              (step) => step.status === "completed",
            ).length;
            return (
              <li key={workflow.id}>
                <div className="workflow-heading">
                  <strong>{workflow.name ?? "Research task"}</strong>
                  <span className={`workflow-status workflow-status-${workflow.status}`}>
                    {workflow.cancel_requested && workflow.status === "running"
                      ? "cancelling"
                      : workflow.status.replace("_", " ")}
                  </span>
                </div>
                <small>{formatTimestamp(workflow.created_at)}</small>
                <progress value={completedSteps} max={workflow.step_count}>
                  {completedSteps} of {workflow.step_count}
                </progress>
                <p className="workflow-current-step">
                  {workflow.current_step_position === null
                    ? `${completedSteps} of ${workflow.step_count} steps`
                    : `Step ${workflow.current_step_position} of ${workflow.step_count}`}
                </p>
                <ol className="workflow-steps" aria-label="Tool activity">
                  {workflow.steps.map((step) => (
                    <li key={step.id}>
                      <div>
                        <strong>{TOOL_LABELS[step.tool_name] ?? step.tool_name}</strong>
                        <span>{step.status.replace("_", " ")}</span>
                      </div>
                      <small>{step.permission}</small>
                      {step.result !== null && (
                        <pre>{JSON.stringify(step.result, null, 2)}</pre>
                      )}
                      {step.error_code !== null && (
                        <p className="muted">Safe failure: {step.error_code}</p>
                      )}
                    </li>
                  ))}
                </ol>
                {workflow.status === "pending" && (
                  <button
                    type="button"
                    className="button button-secondary"
                    onClick={() => void startPending(workflow)}
                    disabled={activeIds.size !== 0 || creating}
                  >
                    Start
                  </button>
                )}
                {(workflow.status === "pending" || workflow.status === "running") && (
                  <button
                    type="button"
                    className="button button-quiet"
                    onClick={() => void cancel(workflow.id)}
                    disabled={workflow.cancel_requested}
                  >
                    Cancel
                  </button>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </aside>
  );
}
