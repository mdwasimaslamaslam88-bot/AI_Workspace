import { afterEach, describe, expect, it, vi } from "vitest";

import {
  applyAppearancePreference,
  readAppearancePreference,
  writeAppearancePreference,
} from "../src/preferences/appearance";

afterEach(() => {
  window.localStorage.clear();
  delete document.documentElement.dataset.theme;
  vi.unstubAllGlobals();
});

describe("appearance preference", () => {
  it("persists only a non-sensitive fixed palette preference", () => {
    writeAppearancePreference("light");
    expect(readAppearancePreference()).toBe("light");
    expect(window.localStorage.getItem("work-station.appearance")).toBe("light");

    writeAppearancePreference("system");
    expect(readAppearancePreference()).toBe("system");
    expect(window.localStorage.getItem("work-station.appearance")).toBeNull();
  });

  it("resolves system, light, and dark themes without storing session data", () => {
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: true })));
    applyAppearancePreference("system");
    expect(document.documentElement.dataset.theme).toBe("dark");

    applyAppearancePreference("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    applyAppearancePreference("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });
});
