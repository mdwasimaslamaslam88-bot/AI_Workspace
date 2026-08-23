import { describe, expect, it } from "vitest";

import { shouldClearTransientSession } from "../src/auth/transient-session";

describe("mobile one-time session visibility", () => {
  it("retains the credential only while the protected screen is active", () => {
    expect(shouldClearTransientSession("active")).toBe(false);
    expect(shouldClearTransientSession("inactive")).toBe(true);
    expect(shouldClearTransientSession("background")).toBe(true);
    expect(shouldClearTransientSession("unknown")).toBe(true);
    expect(shouldClearTransientSession("extension")).toBe(true);
  });
});
