import type { JsonValue } from "@work-station/shared";

const MAX_TOOL_ARGUMENT_CHARACTERS = 16_384;

export function parseBoundedJsonObject(value: string): { [key: string]: JsonValue } {
  if (value.length > MAX_TOOL_ARGUMENT_CHARACTERS) {
    throw new Error("Tool arguments are too large.");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    throw new Error("Tool arguments must be valid JSON.");
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error("Tool arguments must be a JSON object.");
  }
  return parsed as { [key: string]: JsonValue };
}
