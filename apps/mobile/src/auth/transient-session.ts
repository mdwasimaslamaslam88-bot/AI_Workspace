export type MobileAppVisibility =
  | "active"
  | "background"
  | "inactive"
  | "unknown"
  | "extension";

export function shouldClearTransientSession(
  visibility: MobileAppVisibility,
): boolean {
  return visibility !== "active";
}
