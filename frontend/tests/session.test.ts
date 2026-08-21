import { describe, expect, it } from "vitest";

import {
  clearModelPreference,
  clearSessionToken,
  readModelPreference,
  readSessionToken,
  writeModelPreference,
  writeSessionToken,
} from "../src/auth/session";

describe("browser session storage", () => {
  it("stores the bearer only in session storage and clears it on logout", () => {
    writeSessionToken("valid-token");
    expect(readSessionToken()).toBe("valid-token");
    expect(window.localStorage.length).toBe(0);

    clearSessionToken();
    expect(readSessionToken()).toBeNull();
  });

  it("stores and clears only the non-secret model preference", () => {
    writeModelPreference("ollama-local:model");
    expect(readModelPreference()).toBe("ollama-local:model");
    clearModelPreference();
    expect(readModelPreference()).toBeNull();
  });

  it("rejects an empty bearer token", () => {
    expect(() => writeSessionToken("")).toThrow("A bearer token is required.");
    expect(readSessionToken()).toBeNull();
  });
});
