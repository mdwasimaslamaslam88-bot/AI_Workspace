import { describe, expect, it } from "vitest";

import { presenceStates, resolvePresenceState } from "@work-station/shared";


describe("mobile AI presence", () => {
  it("shares the complete companion state contract", () => {
    expect(presenceStates).toHaveLength(7);
    expect(resolvePresenceState({ voice: "LISTENING", working: true })).toBe("LISTENING");
    expect(resolvePresenceState({ generating: true })).toBe("THINKING");
    expect(resolvePresenceState({ working: true })).toBe("WORKING");
    expect(resolvePresenceState({ completed: true })).toBe("DONE");
    expect(resolvePresenceState({ needsInput: true })).toBe("NEEDS INPUT");
    expect(resolvePresenceState({})).toBe("WAITING");
  });
});
