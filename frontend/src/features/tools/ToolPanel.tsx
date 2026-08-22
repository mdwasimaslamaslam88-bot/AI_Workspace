import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";

import type {
  JsonValue,
  ToolDescriptor,
  ToolExecution,
  ToolExecutionRequest,
} from "../../api/contracts";

interface ToolPanelProps {
  activeConversationId: string | null;
  onClose: () => void;
  onLoad: (signal?: AbortSignal) => Promise<{
    tools: ToolDescriptor[];
    executions: ToolExecution[];
  }>;
  onExecute: (
    toolName: string,
    request: ToolExecutionRequest,
    signal?: AbortSignal,
  ) => Promise<ToolExecution>;
}

const TOOL_LABELS: Record<string, string> = {
  calculator: "Calculator",
  local_time: "Local time",
  document_search: "Document search",
  conversation_search: "Conversation search",
  memory_search: "Memory search",
};

function initialInput(toolName: string): string {
  if (toolName !== "local_time") return "";
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

function argumentsFor(toolName: string, input: string): {
  [key: string]: JsonValue;
} {
  if (toolName === "calculator") return { expression: input };
  if (toolName === "local_time") return { timezone: input };
  const limits: Record<string, number> = {
    document_search: 4,
    conversation_search: 10,
    memory_search: 8,
  };
  return { query: input, limit: limits[toolName] ?? 1 };
}

function fieldLabel(toolName: string): string {
  if (toolName === "calculator") return "Arithmetic expression";
  if (toolName === "local_time") return "IANA timezone";
  return "Search query";
}

function formatTimestamp(timestamp: string): string {
  const parsed = new Date(timestamp);
  return Number.isNaN(parsed.valueOf()) ? "" : parsed.toLocaleString();
}

function resultText(result: JsonValue): string {
  return JSON.stringify(result, null, 2);
}

export function ToolPanel({
  activeConversationId,
  onClose,
  onLoad,
  onExecute,
}: ToolPanelProps) {
  const [tools, setTools] = useState<ToolDescriptor[]>([]);
  const [executions, setExecutions] = useState<ToolExecution[]>([]);
  const [selectedName, setSelectedName] = useState("");
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const controllers = useRef(new Set<AbortController>());

  useEffect(() => {
    const activeControllers = controllers.current;
    const controller = new AbortController();
    activeControllers.add(controller);
    void onLoad(controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return;
        setTools(result.tools);
        setExecutions(result.executions);
        const first = result.tools[0]?.name ?? "";
        setSelectedName(first);
        setInput(initialInput(first));
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setError("Local tools could not be loaded.");
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
  }, [onLoad]);

  const selected = useMemo(
    () => tools.find((tool) => tool.name === selectedName) ?? null,
    [selectedName, tools],
  );

  function select(name: string) {
    setSelectedName(name);
    setInput(initialInput(name));
    setError(null);
  }

  async function execute(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (selected === null || !input.trim() || running) return;
    const controller = new AbortController();
    controllers.current.add(controller);
    setRunning(true);
    setError(null);
    try {
      const request: ToolExecutionRequest = {
        arguments: argumentsFor(selected.name, input),
      };
      if (activeConversationId !== null) {
        request.conversation_id = activeConversationId;
      }
      const execution = await onExecute(
        selected.name,
        request,
        controller.signal,
      );
      if (controller.signal.aborted) return;
      setExecutions((current) => [
        execution,
        ...current.filter((item) => item.id !== execution.id),
      ].slice(0, 20));
      if (execution.status !== "completed") {
        setError("The tool stopped safely without a result.");
      }
    } catch {
      if (!controller.signal.aborted) {
        setError("The tool call could not be completed.");
      }
    } finally {
      controllers.current.delete(controller);
      if (!controller.signal.aborted) setRunning(false);
    }
  }

  return (
    <aside className="tool-panel" aria-labelledby="tool-panel-title">
      <header className="tool-panel-header">
        <div>
          <p className="eyebrow">Server-authorized tools</p>
          <h2 id="tool-panel-title">Local tools</h2>
        </div>
        <button type="button" className="button button-quiet" onClick={onClose}>
          Close
        </button>
      </header>
      <p className="field-help">
        Every call is schema-validated, owner-scoped, timed, output-bounded, and
        recorded. Tools have no shell, filesystem, code, or network access.
      </p>

      {loading ? (
        <p className="muted" role="status">Loading tools…</p>
      ) : (
        <>
          <form className="tool-form" onSubmit={(event) => void execute(event)}>
            <div>
              <label htmlFor="tool-name">Tool</label>
              <select
                id="tool-name"
                value={selectedName}
                onChange={(event) => select(event.target.value)}
                disabled={running || tools.length === 0}
              >
                {tools.map((tool) => (
                  <option key={tool.name} value={tool.name}>
                    {TOOL_LABELS[tool.name] ?? tool.name}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="tool-input">{fieldLabel(selectedName)}</label>
              <input
                id="tool-input"
                value={input}
                maxLength={selectedName === "calculator" ? 256 : 500}
                placeholder={
                  selectedName === "calculator"
                    ? "(12 + 8) / 5"
                    : selectedName === "local_time"
                      ? "Asia/Kolkata"
                      : "What should be found?"
                }
                onChange={(event) => setInput(event.target.value)}
                disabled={running || selected === null}
              />
            </div>
            <button
              className="button button-primary"
              disabled={running || selected === null || !input.trim()}
            >
              {running ? "Running…" : "Run tool"}
            </button>
          </form>
          {selected !== null && (
            <p className="tool-policy">
              {selected.description} Permission: {selected.permission}. Deadline:{" "}
              {selected.timeout_seconds}s.
            </p>
          )}
        </>
      )}

      {running && <p role="status">Tool call running within its deadline…</p>}
      {error !== null && (
        <p className="notice notice-error" role="alert">{error}</p>
      )}

      <h3>Recent activity</h3>
      <ol className="tool-history" aria-label="Recent tool activity">
        {!loading && executions.length === 0 && (
          <li className="muted">No tool calls yet.</li>
        )}
        {executions.map((execution) => (
          <li key={execution.id}>
            <div className="tool-execution-heading">
              <strong>{TOOL_LABELS[execution.tool_name] ?? execution.tool_name}</strong>
              <span className={`tool-status tool-status-${execution.status}`}>
                {execution.status.replace("_", " ")}
              </span>
            </div>
            <small>
              {execution.permission} · {formatTimestamp(execution.started_at)}
              {execution.duration_ms !== null ? ` · ${execution.duration_ms} ms` : ""}
            </small>
            {execution.status === "completed" && execution.result !== null && (
              <pre>{resultText(execution.result)}</pre>
            )}
            {execution.status !== "completed" && execution.error_code !== null && (
              <p className="muted">Safe failure: {execution.error_code}</p>
            )}
          </li>
        ))}
      </ol>
    </aside>
  );
}
