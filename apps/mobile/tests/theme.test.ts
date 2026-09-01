import { describe, expect, it } from "vitest";

import { resolvedAppearanceScheme, workStationColors } from "../src/theme/colors";

describe("mobile appearance", () => {
  it("uses a readable dark palette for dark or unavailable system themes", () => {
    expect(workStationColors("dark")).toEqual(workStationColors(null));
    expect(workStationColors("dark").text).not.toBe(workStationColors("dark").background);
  });

  it("provides a distinct light palette without changing semantic roles", () => {
    const light = workStationColors("light");
    const dark = workStationColors("dark");

    expect(light.background).not.toBe(dark.background);
    expect(light.text).not.toBe(light.background);
    expect(light.accent).not.toBe(light.onAccent);
    expect(light.danger).not.toBe(light.dangerSoft);
  });

  it("honors explicit light/dark choices and follows the system otherwise", () => {
    expect(resolvedAppearanceScheme("light", "dark")).toBe("light");
    expect(resolvedAppearanceScheme("dark", "light")).toBe("dark");
    expect(resolvedAppearanceScheme("system", "light")).toBe("light");
    expect(resolvedAppearanceScheme("system", null)).toBe("dark");
  });
});
