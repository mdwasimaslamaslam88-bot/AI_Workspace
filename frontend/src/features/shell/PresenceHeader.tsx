export type PresenceState = "WORKING" | "WAITING" | "NEEDS INPUT";

interface PresenceHeaderProps {
  state: PresenceState;
  modelName: string | null;
  onAsk: () => void;
}

export function PresenceHeader({ state, modelName, onAsk }: PresenceHeaderProps) {
  const stateClass = state.toLowerCase().replace(" ", "-");
  return (
    <section className="presence-header" aria-labelledby="presence-title">
      <div className={`companion-avatar companion-avatar-${stateClass}`}>
        <img src="/images/ai-companion-avatar-v1.png" alt="AI companion" />
        <span aria-hidden="true" />
      </div>
      <div className="presence-copy">
        <p className="eyebrow">PERSONAL AI OPERATING SYSTEM</p>
        <h2 id="presence-title">What shall we accomplish?</h2>
        <p>
          Plan, execute, and verify with your private AI.
          {modelName === null ? " Select a ready model to begin." : ` Ready model: ${modelName}.`}
        </p>
      </div>
      <div className="presence-actions">
        <span className={`presence-state presence-state-${stateClass}`} aria-label="AI presence status" aria-live="polite">
          {state}
        </span>
        <button type="button" className="button button-primary" onClick={onAsk}>
          Ask AI Anything
        </button>
      </div>
    </section>
  );
}
