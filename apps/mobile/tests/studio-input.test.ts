import { describe, expect, it } from "vitest";

import { parseBoundedJsonObject } from "../src/studio/input";

describe("private Studio input", () => {
  it("accepts an explicit bounded JSON object", () => {
    expect(parseBoundedJsonObject('{"expression":"2+2","limit":4}')).toEqual({
      expression: "2+2",
      limit: 4,
    });
  });

  it("rejects invalid JSON, arrays, and oversized input without echoing it", () => {
    expect(() => parseBoundedJsonObject("private invalid input")).toThrow(
      "Tool arguments must be valid JSON.",
    );
    expect(() => parseBoundedJsonObject("[]")).toThrow(
      "Tool arguments must be a JSON object.",
    );
    expect(() => parseBoundedJsonObject(`{"value":"${"x".repeat(16_384)}"}`)).toThrow(
      "Tool arguments are too large.",
    );
  });
});
