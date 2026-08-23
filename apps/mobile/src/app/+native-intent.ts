import { parsePrivateDeepLink } from "@work-station/shared";

export function redirectSystemPath({ path }: { path: string; initial: boolean }): string {
  const target = parsePrivateDeepLink(path);
  if (target === "settings") return "/settings";
  if (target === "chat") return "/";
  if (
    target === "studio" ||
    target === "memory" ||
    target === "tools" ||
    target === "workflows"
  ) {
    return "/studio";
  }
  return "/";
}
