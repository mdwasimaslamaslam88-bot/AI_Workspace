import { describe, expect, it } from "vitest";

import { redirectSystemPath } from "../src/app/+native-intent";

describe("mobile private deep links", () => {
  it.each([
    ["work-station://chat", "/"],
    ["work-station://settings", "/settings"],
    ["work-station://studio", "/studio"],
    ["work-station://memory", "/studio"],
    ["work-station://tools", "/studio"],
    ["work-station://workflows", "/studio"],
  ] as const)("routes %s to %s", (path, expected) => {
    expect(redirectSystemPath({ path, initial: true })).toBe(expected);
  });

  it.each([
    "work-station://settings?unexpected=value",
    "work-station://settings/private-id",
    "https://example.invalid/private",
    "not a URL",
  ])("fails closed to Chats for %s", (path) => {
    expect(redirectSystemPath({ path, initial: false })).toBe("/");
  });
});
