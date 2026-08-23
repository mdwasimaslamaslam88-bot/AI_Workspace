import { describe, expect, it } from "vitest";

import { parsePrivateDeepLink } from "@work-station/shared";

describe("private deep-link contract", () => {
  it.each([
    ["work-station://chat", "chat"],
    ["work-station://settings/", "settings"],
    ["work-station:///studio", "studio"],
    ["work-station://memory", "memory"],
    ["work-station://tools", "tools"],
    ["work-station://workflows", "workflows"],
  ] as const)("maps %s to the fixed %s section", (value, expected) => {
    expect(parsePrivateDeepLink(value)).toBe(expected);
  });

  it.each([
    "",
    " work-station://chat",
    "https://work-station.invalid/chat",
    "work-station://owner@settings",
    "work-station://settings:42",
    "work-station://settings?unexpected=value",
    "work-station://settings#private",
    "work-station://settings/private-id",
    "work-station://unknown",
    `work-station://${"x".repeat(300)}`,
  ])("rejects non-routable input without echoing it: %s", (value) => {
    expect(parsePrivateDeepLink(value)).toBeNull();
  });
});
