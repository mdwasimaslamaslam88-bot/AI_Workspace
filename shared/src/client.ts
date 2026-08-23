export type ClientPlatform = "web" | "desktop" | "android" | "ios";

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
