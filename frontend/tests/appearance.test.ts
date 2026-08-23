import { afterEach, describe, expect, it, vi } from "vitest";

import {
  applyAppearancePreference,
  readAppearancePreference,
  writeAppearancePreference,
} from "../src/preferences/appearance";
import "../src/styles.css";

function relativeLuminance(color: string): number {
  const channels = color.match(/[0-9a-f]{2}/gi)?.map((value) =>
    Number.parseInt(value, 16) / 255,
  );
  if (channels === undefined || channels.length !== 3) {
    throw new Error("Expected a six-digit CSS color.");
  }
  const [red, green, blue] = channels.map((value) =>
    value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
}

function contrastRatio(foreground: string, background: string): number {
  const foregroundLuminance = relativeLuminance(foreground);
  const backgroundLuminance = relativeLuminance(background);
  return (
    (Math.max(foregroundLuminance, backgroundLuminance) + 0.05) /
    (Math.min(foregroundLuminance, backgroundLuminance) + 0.05)
  );
}

function themeColor(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

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

  it.each(["dark", "light"] as const)(
    "keeps the %s theme's essential text and control pairs at WCAG AA contrast",
    (theme) => {
      document.documentElement.dataset.theme = theme;
      for (const [foreground, background] of [
        ["--text", "--card"],
        ["--muted", "--card"],
        ["--accent-contrast", "--accent"],
        ["--accent-contrast", "--accent-hover"],
        ["--danger", "--danger-bg"],
      ]) {
        expect(
          contrastRatio(themeColor(foreground), themeColor(background)),
          `${theme} ${foreground} on ${background}`,
        ).toBeGreaterThanOrEqual(4.5);
      }
    },
  );
});
