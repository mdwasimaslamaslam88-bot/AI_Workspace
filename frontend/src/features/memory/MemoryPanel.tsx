import { type FormEvent, useEffect, useRef, useState } from "react";

import type {
  MemoryCategory,
  MemoryCreateRequest,
  MemorySetting,
  PersonalMemory,
} from "../../api/contracts";


interface MemoryPanelProps {
  onClose: () => void;
  onLoad: (signal?: AbortSignal) => Promise<{
    memories: PersonalMemory[];
    setting: MemorySetting;
  }>;
  onCreate: (
    request: MemoryCreateRequest,
    signal?: AbortSignal,
  ) => Promise<PersonalMemory>;
  onForget: (
    memoryId: string,
    signal?: AbortSignal,
  ) => Promise<PersonalMemory>;
  onSetEnabled: (
    enabled: boolean,
    signal?: AbortSignal,
  ) => Promise<MemorySetting>;
}

const CATEGORY_LABELS: Record<MemoryCategory, string> = {
  preference: "Preference",
  fact: "Fact",
  instruction: "Instruction",
  project_context: "Project / context",
};

function formatTimestamp(timestamp: string): string {
  const parsed = new Date(timestamp);
  return Number.isNaN(parsed.valueOf()) ? "" : parsed.toLocaleString();
}

export function MemoryPanel({
  onClose,
  onLoad,
  onCreate,
  onForget,
  onSetEnabled,
}: MemoryPanelProps) {
  const [memories, setMemories] = useState<PersonalMemory[]>([]);
  const [setting, setSetting] = useState<MemorySetting | null>(null);
  const [category, setCategory] = useState<MemoryCategory>("preference");
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const controllers = useRef(new Set<AbortController>());

  useEffect(() => {
    const activeControllers = controllers.current;
    const controller = new AbortController();
    activeControllers.add(controller);
    void onLoad(controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return;
        setMemories(result.memories);
        setSetting(result.setting);
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setError("Personal memory could not be loaded.");
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

  async function run<T>(operation: (signal: AbortSignal) => Promise<T>): Promise<T | null> {
    const controller = new AbortController();
    controllers.current.add(controller);
    setBusy(true);
    setError(null);
    try {
      return await operation(controller.signal);
    } catch {
      if (!controller.signal.aborted) {
        setError("Personal memory could not be updated.");
      }
      return null;
    } finally {
      controllers.current.delete(controller);
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!content.trim() || busy) return;
    const memory = await run((signal) =>
      onCreate({ category, content }, signal),
    );
    if (memory === null) return;
    setMemories((current) => [memory, ...current]);
    setContent("");
  }

  async function forget(memoryId: string) {
    const forgotten = await run((signal) => onForget(memoryId, signal));
    if (forgotten === null) return;
    setMemories((current) =>
      current.map((memory) =>
        memory.id === forgotten.id ? forgotten : memory,
      ),
    );
  }

  async function setEnabled(enabled: boolean) {
    const updated = await run((signal) => onSetEnabled(enabled, signal));
    if (updated !== null) setSetting(updated);
  }

  return (
    <aside className="memory-panel" aria-labelledby="memory-panel-title">
      <header className="memory-panel-header">
        <div>
          <p className="eyebrow">Personal memory</p>
          <h2 id="memory-panel-title">What your AI remembers</h2>
        </div>
        <button type="button" className="button button-quiet" onClick={onClose}>
          Close
        </button>
      </header>

      <p className="field-help">
        Only entries you explicitly save appear here. Current instructions always
        override stored memory.
      </p>

      {loading ? (
        <p className="muted" role="status">Loading memory…</p>
      ) : (
        <>
          <label className="memory-toggle">
            <input
              type="checkbox"
              checked={setting?.enabled ?? true}
              onChange={(event) => void setEnabled(event.target.checked)}
              disabled={busy}
            />
            Use saved memory in responses
          </label>

          <form className="memory-form" onSubmit={(event) => void create(event)}>
            <div>
              <label htmlFor="memory-category">Category</label>
              <select
                id="memory-category"
                value={category}
                onChange={(event) =>
                  setCategory(event.target.value as MemoryCategory)
                }
                disabled={busy}
              >
                {Object.entries(CATEGORY_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="memory-content">Memory</label>
              <textarea
                id="memory-content"
                value={content}
                maxLength={2_000}
                rows={3}
                placeholder="Save a preference, fact, instruction, or project context"
                onChange={(event) => setContent(event.target.value)}
                disabled={busy}
              />
            </div>
            <button
              className="button button-primary"
              disabled={busy || !content.trim()}
            >
              Save explicitly
            </button>
          </form>

          {error !== null && (
            <p className="notice notice-error" role="alert">{error}</p>
          )}

          <ul className="memory-list" aria-label="Saved memories">
            {memories.length === 0 && (
              <li className="muted">No personal memories saved.</li>
            )}
            {memories.map((memory) => (
              <li key={memory.id}>
                <div className="memory-item-heading">
                  <strong>{CATEGORY_LABELS[memory.category]}</strong>
                  <time dateTime={memory.updated_at}>
                    {formatTimestamp(memory.updated_at)}
                  </time>
                </div>
                {memory.state === "deleted" ? (
                  <span className="attachment-tombstone">Forgotten memory</span>
                ) : (
                  <>
                    <p>{memory.content}</p>
                    <div className="memory-provenance">
                      Explicit user entry · {formatTimestamp(memory.created_at)}
                    </div>
                    <button
                      type="button"
                      className="button button-quiet"
                      onClick={() => void forget(memory.id)}
                      disabled={busy}
                    >
                      Forget
                    </button>
                  </>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </aside>
  );
}
