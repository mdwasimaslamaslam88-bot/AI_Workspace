import { type FormEvent, useEffect, useMemo, useState } from "react";

import type {
  CreativeCapabilities,
  CreativeExperience,
  CreativeExperienceCreateRequest,
  CreativeTurnCreateRequest,
} from "../../api/contracts";


interface CreativePanelProps {
  onClose: () => void;
  onCapabilities: (signal?: AbortSignal) => Promise<CreativeCapabilities>;
  onLoad: (signal?: AbortSignal) => Promise<CreativeExperience[]>;
  onGet: (id: string, signal?: AbortSignal) => Promise<CreativeExperience>;
  onCreate: (request: CreativeExperienceCreateRequest, signal?: AbortSignal) => Promise<CreativeExperience>;
  onTurn: (id: string, request: CreativeTurnCreateRequest, signal?: AbortSignal) => Promise<CreativeExperience>;
  onComplete: (id: string, signal?: AbortSignal) => Promise<CreativeExperience>;
}

function replaceExperience(current: CreativeExperience[], value: CreativeExperience) {
  return [value, ...current.filter((experience) => experience.id !== value.id)].sort(
    (left, right) => right.updated_at.localeCompare(left.updated_at),
  );
}

export function CreativePanel({
  onClose, onCapabilities, onLoad, onGet, onCreate, onTurn, onComplete,
}: CreativePanelProps) {
  const [experiences, setExperiences] = useState<CreativeExperience[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [capabilities, setCapabilities] = useState<CreativeCapabilities | null>(null);
  const [mode, setMode] = useState<CreativeExperienceCreateRequest["mode"]>("story");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const selected = useMemo(
    () => experiences.find((experience) => experience.id === selectedId) ?? experiences[0] ?? null,
    [experiences, selectedId],
  );

  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([onLoad(controller.signal), onCapabilities(controller.signal)])
      .then(([values, available]) => {
        if (controller.signal.aborted) return;
        setExperiences(values);
        setCapabilities(available);
        setSelectedId(values[0]?.id ?? null);
      })
      .catch(() => {
        if (!controller.signal.aborted) setNotice("Creative workspace could not be loaded.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [onCapabilities, onLoad]);

  async function perform(operation: (signal: AbortSignal) => Promise<CreativeExperience>) {
    if (busy) return;
    const controller = new AbortController();
    setBusy(true);
    setNotice(null);
    try {
      const value = await operation(controller.signal);
      setExperiences((current) => replaceExperience(current, value));
      setSelectedId(value.id);
      return value;
    } catch {
      setNotice("The creative action was rejected or could not be verified.");
    } finally {
      setBusy(false);
    }
  }

  async function createExperience(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    const created = await perform((signal) => onCreate({
      mode,
      title: String(values.get("title") ?? "").trim(),
      premise: String(values.get("premise") ?? "").trim(),
      genre: String(values.get("genre") ?? "").trim(),
      language: String(values.get("language") ?? "").trim(),
      character_name: mode === "character" ? String(values.get("character_name") ?? "").trim() : null,
    }, signal));
    if (created !== undefined) form.reset();
  }

  async function addTurn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (selected === null) return;
    const form = event.currentTarget;
    const values = new FormData(form);
    const value = await perform((signal) => onTurn(selected.id, {
      owner_input: String(values.get("owner_input") ?? "").trim(),
    }, signal));
    if (value !== undefined) form.reset();
  }

  async function selectExperience(id: string) {
    setSelectedId(id);
    await perform((signal) => onGet(id, signal));
  }

  return <aside className="workflow-panel creative-panel" aria-labelledby="creative-panel-title">
    <header className="workflow-panel-header">
      <div><p className="eyebrow">Creative studio</p><h2 id="creative-panel-title">Verified experiences</h2></div>
      <button type="button" className="button button-quiet" onClick={onClose}>Close</button>
    </header>
    <p className="field-help">Interactive stories, text games, and fictional-character experiences use the local Agent OS. Every stored response is the untouched verified artifact and is isolated to this owner.</p>
    {capabilities !== null && <p className="notice">Local stories, games, and fictional characters: ready. General-audience safety: enforced. Image and voice remain runtime-dependent; video, animation, generative audio editing, and adult experiences remain external dependencies.</p>}
    {notice !== null && <p className="notice notice-error" role="alert">{notice}</p>}
    <details open={experiences.length === 0}>
      <summary>Create an experience</summary>
      <form className="workflow-form" onSubmit={(event) => void createExperience(event)}>
        <label>Mode<select name="mode" value={mode} onChange={(event) => setMode(event.target.value as CreativeExperienceCreateRequest["mode"])}><option value="story">Interactive story</option><option value="game">Text game</option><option value="character">Fictional character</option></select></label>
        <label>Title<input name="title" required maxLength={160} /></label>
        <label>Premise<textarea name="premise" required maxLength={4_000} /></label>
        <label>Genre<input name="genre" required maxLength={80} /></label>
        <label>Language<input name="language" required defaultValue="en" pattern="[A-Za-z][A-Za-z0-9-]{1,34}" /></label>
        {mode === "character" && <label>Fictional character name<input name="character_name" required maxLength={120} /></label>}
        <button disabled={busy} className="button button-primary">Create experience</button>
      </form>
    </details>
    {loading ? <p role="status">Loading creative experiences…</p> : experiences.length > 0 && <label>Active experience<select value={selected?.id ?? ""} onChange={(event) => void selectExperience(event.target.value)}>{experiences.map((experience) => <option key={experience.id} value={experience.id}>{experience.title}</option>)}</select></label>}
    {selected !== null && <section aria-label="Creative experience">
      <div className="finance-summary"><strong>{selected.title}</strong><span>{selected.mode}</span><span>{selected.status}</span><span>{selected.turn_count}/100 turns</span></div>
      <p>{selected.premise}</p>
      {selected.turns.length === 0 ? <p className="field-help">Begin when ready. No generation is claimed until a verified turn completes.</p> : <ol className="creative-turns">{selected.turns.map((turn) => <li key={turn.id}>
        <p><strong>You</strong> {turn.owner_input}</p>
        <p><strong>AI</strong> {turn.output}</p>
        <p className="field-help">Verified artifact {turn.output_sha256.slice(0, 12)} · model {turn.model_id}</p>
      </li>)}</ol>}
      {selected.status === "active" && <form className="workflow-form creative-turn-form" onSubmit={(event) => void addTurn(event)}>
        <label>Your next move<textarea name="owner_input" required maxLength={4_000} /></label>
        <button disabled={busy || selected.turn_count >= 100} className="button button-primary">Generate verified turn</button>
        <button type="button" disabled={busy || selected.turn_count === 0} className="button button-quiet" onClick={() => void perform((signal) => onComplete(selected.id, signal))}>Complete experience</button>
      </form>}
    </section>}
    <section className="notice"><strong>Honest capability boundary</strong><p>This workspace does not claim local video, animation, generative audio editing, or protected adult operation. Those require separately verified runtimes and, where applicable, jurisdiction, age, and consent controls.</p></section>
  </aside>;
}
