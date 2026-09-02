import { describe, expect, it } from "vitest";

import {
  presenceStateForAgentStatus,
  presenceStates,
  resolvePresenceState,
} from "@work-station/shared";


describe("truthful AI presence state", () => {
  it("defines every product state exactly once", () => {
    expect(presenceStates).toEqual([
      "LISTENING",
      "THINKING",
      "WORKING",
      "WAITING",
      "VERIFYING",
      "DONE",
      "NEEDS INPUT",
    ]);
    expect(new Set(presenceStates).size).toBe(presenceStates.length);
  });

  it("maps actual Agent OS lifecycle states without invented progress", () => {
    expect(presenceStateForAgentStatus("queued")).toBe("THINKING");
    expect(presenceStateForAgentStatus("planning")).toBe("THINKING");
    expect(presenceStateForAgentStatus("running")).toBe("WORKING");
    expect(presenceStateForAgentStatus("retrying")).toBe("WORKING");
    expect(presenceStateForAgentStatus("verifying")).toBe("VERIFYING");
    expect(presenceStateForAgentStatus("completed")).toBe("DONE");
    expect(presenceStateForAgentStatus("failed")).toBe("NEEDS INPUT");
    expect(presenceStateForAgentStatus("timed_out")).toBe("NEEDS INPUT");
    expect(presenceStateForAgentStatus("cancelled")).toBe("WAITING");
  });

  it("gives capture and errors priority over background activity", () => {
    expect(resolvePresenceState({ voice: "LISTENING", generating: true })).toBe("LISTENING");
    expect(resolvePresenceState({ needsInput: true, generating: true })).toBe("NEEDS INPUT");
    expect(resolvePresenceState({ agent: "verifying", generating: true })).toBe("VERIFYING");
    expect(resolvePresenceState({ generating: true })).toBe("THINKING");
    expect(resolvePresenceState({ working: true })).toBe("WORKING");
    expect(resolvePresenceState({ completed: true })).toBe("DONE");
    expect(resolvePresenceState({})).toBe("WAITING");
  });
});
