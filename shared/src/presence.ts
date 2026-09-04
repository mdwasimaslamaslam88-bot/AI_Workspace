export const presenceStates = [
  "LISTENING",
  "THINKING",
  "WORKING",
  "WAITING",
  "VERIFYING",
  "DONE",
  "NEEDS INPUT",
] as const;

export type PresenceState = typeof presenceStates[number];

export type PresenceAgentStatus =
  | "queued"
  | "needs_approval"
  | "planning"
  | "running"
  | "paused"
  | "verifying"
  | "retrying"
  | "completed"
  | "failed"
  | "cancelled"
  | "timed_out";

export interface PresenceSignals {
  voice?: PresenceState | null;
  agent?: PresenceAgentStatus | null;
  generating?: boolean;
  working?: boolean;
  completed?: boolean;
  needsInput?: boolean;
}

export function presenceStateForAgentStatus(
  status: PresenceAgentStatus | null | undefined,
): PresenceState | null {
  if (status === "queued" || status === "planning") return "THINKING";
  if (status === "needs_approval") return "NEEDS INPUT";
  if (status === "paused") return "WAITING";
  if (status === "running" || status === "retrying") return "WORKING";
  if (status === "verifying") return "VERIFYING";
  if (status === "completed") return "DONE";
  if (status === "failed" || status === "timed_out") return "NEEDS INPUT";
  if (status === "cancelled") return "WAITING";
  return null;
}

export function resolvePresenceState(signals: PresenceSignals): PresenceState {
  if (signals.voice === "LISTENING") return "LISTENING";
  if (signals.needsInput || signals.voice === "NEEDS INPUT") return "NEEDS INPUT";
  if (signals.voice !== null && signals.voice !== undefined) return signals.voice;
  const agentState = presenceStateForAgentStatus(signals.agent);
  if (agentState !== null) return agentState;
  if (signals.generating) return "THINKING";
  if (signals.working) return "WORKING";
  if (signals.completed) return "DONE";
  return "WAITING";
}
