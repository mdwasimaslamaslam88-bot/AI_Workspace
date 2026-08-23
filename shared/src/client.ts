export type ClientPlatform = "web" | "desktop" | "android" | "ios";

export type PrivateDeepLinkTarget =
  | "chat"
  | "settings"
  | "studio"
  | "memory"
  | "tools"
  | "workflows";

const PRIVATE_DEEP_LINK_TARGETS = new Set<PrivateDeepLinkTarget>([
  "chat",
  "settings",
  "studio",
  "memory",
  "tools",
  "workflows",
]);

export function parsePrivateDeepLink(value: string): PrivateDeepLinkTarget | null {
  if (
    typeof value !== "string" ||
    value.length < 1 ||
    value.length > 256 ||
    value !== value.trim()
  ) {
    return null;
  }
  try {
    const link = new URL(value);
    if (
      link.protocol !== "work-station:" ||
      link.username !== "" ||
      link.password !== "" ||
      link.port !== "" ||
      link.search !== "" ||
      link.hash !== ""
    ) {
      return null;
    }
    const target = link.hostname === ""
      ? link.pathname.startsWith("/")
        ? link.pathname.slice(1)
        : link.pathname
      : link.pathname === "" || link.pathname === "/"
        ? link.hostname
        : "";
    return PRIVATE_DEEP_LINK_TARGETS.has(target as PrivateDeepLinkTarget)
      ? target as PrivateDeepLinkTarget
      : null;
  } catch {
    return null;
  }
}

export type ConnectionState =
  | "connecting"
  | "connected"
  | "offline"
  | "reconnecting"
  | "authentication_required"
  | "backend_unavailable"
  | "runtime_unavailable";

export interface SessionDescriptor {
  platform: ClientPlatform;
  connection: ConnectionState;
  authenticated: boolean;
}

export interface ClientError {
  kind:
    | "authentication"
    | "not_found"
    | "conflict"
    | "too_large"
    | "validation"
    | "busy"
    | "unavailable"
    | "server"
    | "network"
    | "cancelled"
    | "unexpected";
  status: number | null;
  requestId: string | null;
  code: string | null;
  safeMessage: string;
}

/**
 * Transport-neutral lifecycle events. They describe real request state and do
 * not imply token streaming when the backend returns a bounded final response.
 */
export type GenerationLifecycleEvent =
  | { type: "started"; conversationId: string }
  | { type: "completed"; conversationId: string; messageId: number }
  | { type: "cancelled"; conversationId: string }
  | { type: "failed"; conversationId: string; error: ClientError };
