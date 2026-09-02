import {
  type BackendErrorEnvelope,
  type AccessTokenRotation,
  type AgentOSCapabilities,
  type AgentRun,
  type AgentRunCreateRequest,
  type AgentRunEvent,
  type AgentRunPage,
  type Asset,
  type ConversationCreateRequest,
  type ConversationCreateResponse,
  type ConversationCursor,
  type ConversationForkRequest,
  type ConversationPage,
  type ConversationRenameRequest,
  type ConversationSearchRequest,
  type ConversationStateUpdateRequest,
  type ConversationSummary,
  type ConversationTextGenerationRequest,
  type ConversationTextGenerationResponse,
  type CurrentUser,
  type ExternalAISettings,
  type ExternalProviderUpsertRequest,
  type FeatureRegistry,
  type IndexedDocument,
  type ImageEditingRequest,
  type ImageGenerationRequest,
  type ImageOperation,
  type LocalModelPage,
  type MemoryCreateRequest,
  type MemoryPage,
  type MemorySetting,
  type MessagePage,
  type PersonalMemory,
  type ProductCapabilityPage,
  type SelfUpdateStatus,
  type SystemDiagnostics,
  type UserSession,
  type UserSessionCreateRequest,
  type UserSessionPage,
  type UserSessionProvision,
  type UserSessionUpdateRequest,
  type ToolDescriptorPage,
  type ToolExecution,
  type ToolExecutionPage,
  type ToolExecutionRequest,
  type Workflow,
  type WorkflowCreateRequest,
  type WorkflowPage,
  type VoiceSynthesis,
  type VoiceSynthesisRequest,
  type VoiceTranscription,
  type VoiceTranscriptionRequest,
  parseIndexedDocument,
  parseAccessTokenRotation,
  parseAgentOSCapabilities,
  parseAgentRun,
  parseAgentRunEvent,
  parseAgentRunPage,
  parseImageOperation,
  parseAsset,
  parseConversation,
  parseConversationCreateResponse,
  parseConversationPage,
  parseCurrentUser,
  parseExternalAISettings,
  parseFeatureRegistry,
  parseGenerationResponse,
  parseMemoryPage,
  parseMemorySetting,
  parseMessagePage,
  parseModelPage,
  parsePersonalMemory,
  parseProductCapabilityPage,
  parseSelfUpdateStatus,
  parseSystemDiagnostics,
  parseUserSession,
  parseUserSessionPage,
  parseUserSessionProvision,
  parseToolDescriptorPage,
  parseToolExecution,
  parseToolExecutionPage,
  parseWorkflow,
  parseWorkflowPage,
  parseVoiceSynthesis,
  parseVoiceTranscription,
} from "./contracts";

export type ApiErrorKind =
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

const publicErrorCodes = new Set([
  "HTTP_ERROR",
  "VALIDATION_ERROR",
  "INTERNAL_SERVER_ERROR",
]);

const statusErrors: Record<number, { kind: ApiErrorKind; message: string }> = {
  400: { kind: "validation", message: "The request could not be accepted." },
  401: { kind: "authentication", message: "Authentication failed." },
  403: { kind: "authentication", message: "This action is not authorized." },
  404: { kind: "not_found", message: "The requested item was not found." },
  409: {
    kind: "conflict",
    message: "The conversation changed. Refresh and try again.",
  },
  413: { kind: "too_large", message: "The request is too large." },
  422: { kind: "validation", message: "The request could not be validated." },
  429: { kind: "busy", message: "Local generation is busy. Try again shortly." },
  500: { kind: "server", message: "The backend could not complete the request." },
  503: {
    kind: "unavailable",
    message: "The local model runtime is unavailable.",
  },
  504: {
    kind: "unavailable",
    message: "The local operation timed out.",
  },
};

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  readonly requestId: string | null;
  readonly code: string | null;

  constructor(
    kind: ApiErrorKind,
    message: string,
    options: {
      status?: number | null;
      requestId?: string | null;
      code?: string | null;
    } = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = options.status ?? null;
    this.requestId = options.requestId ?? null;
    this.code = options.code ?? null;
  }
}

export function normalizeApiBaseUrl(
  value: string | undefined,
  browserOrigin?: string,
): string {
  let inferredOrigin = "http://127.0.0.1:8000";
  if (value?.trim() === "" || value === undefined) {
    try {
      const browserUrl = new URL(browserOrigin ?? "");
      const localDevelopment =
        ["localhost", "127.0.0.1", "[::1]"].includes(browserUrl.hostname) &&
        browserUrl.port !== "8000";
      if (
        ["http:", "https:"].includes(browserUrl.protocol) &&
        !localDevelopment
      ) {
        inferredOrigin = browserUrl.origin;
      }
    } catch {
      // Desktop asset protocols and test environments use the loopback API.
    }
  }
  const candidate = value?.trim() || inferredOrigin;
  let parsed: URL;
  try {
    parsed = new URL(candidate);
  } catch {
    throw new Error("VITE_API_BASE_URL must be a valid HTTP origin.");
  }
  if (
    !["http:", "https:"].includes(parsed.protocol) ||
    parsed.username !== "" ||
    parsed.password !== "" ||
    parsed.search !== "" ||
    parsed.hash !== "" ||
    !["", "/"].includes(parsed.pathname)
  ) {
    throw new Error(
      "VITE_API_BASE_URL must be an HTTP origin without credentials or a path.",
    );
  }
  if (
    parsed.protocol === "http:" &&
    !["localhost", "127.0.0.1", "[::1]"].includes(parsed.hostname)
  ) {
    throw new Error("Remote VITE_API_BASE_URL origins must use HTTPS.");
  }
  return parsed.origin;
}

export const API_BASE_URL = normalizeApiBaseUrl(
  import.meta.env.VITE_API_BASE_URL,
  typeof window === "undefined" ? undefined : window.location.origin,
);

type JsonDecoder<T> = (value: unknown) => T;
type FetchImplementation = typeof fetch;
type XhrFactory = () => XMLHttpRequest;

export interface UploadProgress {
  loaded: number;
  total: number | null;
}

interface ApiClientOptions {
  baseUrl?: string;
  fetchImplementation?: FetchImplementation;
  onUnauthorized?: () => void;
  xhrFactory?: XhrFactory;
}

interface RequestOptions<T> {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  decode: JsonDecoder<T>;
}

function safeEnvelopeCode(value: unknown): string | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return null;
  }
  const envelope = value as Partial<BackendErrorEnvelope>;
  const code = envelope.error?.code;
  return typeof code === "string" && publicErrorCodes.has(code) ? code : null;
}

async function readSafeErrorCode(response: Response): Promise<string | null> {
  try {
    return safeEnvelopeCode(await response.json());
  } catch {
    return null;
  }
}

function errorForStatus(
  status: number,
  requestId: string | null,
  code: string | null,
): ApiError {
  const normalized = statusErrors[status] ?? {
    kind: "unexpected" as const,
    message: "The backend rejected the request.",
  };
  return new ApiError(normalized.kind, normalized.message, {
    status,
    requestId,
    code,
  });
}

function attachmentErrorForStatus(
  status: number,
  requestId: string | null,
  code: string | null,
): ApiError {
  if (status === 503 || status === 507) {
    return new ApiError("unavailable", "Attachment storage is unavailable.", {
      status,
      requestId,
      code,
    });
  }
  return errorForStatus(status, requestId, code);
}

export class ApiClient {
  readonly #baseUrl: string;
  readonly #token: string;
  readonly #fetch: FetchImplementation;
  readonly #onUnauthorized: (() => void) | undefined;
  readonly #xhrFactory: XhrFactory;

  constructor(token: string, options: ApiClientOptions = {}) {
    if (token.length === 0) throw new Error("A bearer token is required.");
    this.#token = token;
    this.#baseUrl = normalizeApiBaseUrl(options.baseUrl ?? API_BASE_URL);
    this.#fetch = options.fetchImplementation ?? fetch;
    this.#onUnauthorized = options.onUnauthorized;
    this.#xhrFactory = options.xhrFactory ?? (() => new XMLHttpRequest());
  }

  getProductCapabilities(signal?: AbortSignal): Promise<ProductCapabilityPage> {
    return this.#request("api/v1/ai/capabilities", {
      signal,
      decode: parseProductCapabilityPage,
    });
  }

  getFeatureRegistry(signal?: AbortSignal): Promise<FeatureRegistry> {
    return this.#request("api/v1/features", {
      signal,
      decode: parseFeatureRegistry,
    });
  }

  getAgentOSCapabilities(signal?: AbortSignal): Promise<AgentOSCapabilities> {
    return this.#request("api/v1/agent-os/capabilities", {
      signal,
      decode: parseAgentOSCapabilities,
    });
  }

  listAgentRuns(signal?: AbortSignal): Promise<AgentRunPage> {
    return this.#request("api/v1/agent-os/runs?limit=20", {
      signal,
      decode: parseAgentRunPage,
    });
  }

  createAgentRun(
    request: AgentRunCreateRequest,
    signal?: AbortSignal,
  ): Promise<AgentRun> {
    return this.#request("api/v1/agent-os/runs", {
      method: "POST",
      body: request,
      signal,
      decode: parseAgentRun,
    });
  }

  cancelAgentRun(runId: string, signal?: AbortSignal): Promise<AgentRun> {
    return this.#request(`api/v1/agent-os/runs/${encodeURIComponent(runId)}/cancel`, {
      method: "POST",
      signal,
      decode: parseAgentRun,
    });
  }

  async streamAgentRunEvents(
    runId: string,
    onEvent: (event: AgentRunEvent) => void,
    signal: AbortSignal,
    after = 0,
  ): Promise<void> {
    let sequence = after;
    while (!signal.aborted) {
      const url = new URL(
        `api/v1/agent-os/runs/${encodeURIComponent(runId)}/events?after=${sequence}`,
        `${this.#baseUrl}/`,
      );
      let response: Response;
      try {
        response = await this.#fetch.call(globalThis, url, {
          headers: {
            Accept: "text/event-stream",
            Authorization: `Bearer ${this.#token}`,
          },
          signal,
        });
      } catch (error) {
        if (
          signal.aborted ||
          (error instanceof DOMException && error.name === "AbortError")
        ) return;
        throw new ApiError("network", "Could not reach the local backend.");
      }
      const requestId = response.headers.get("X-Request-ID");
      if (!response.ok) {
        const code = await readSafeErrorCode(response);
        if (response.status === 401) this.#onUnauthorized?.();
        throw errorForStatus(response.status, requestId, code);
      }
      if (
        !response.headers.get("content-type")?.startsWith("text/event-stream") ||
        response.body === null
      ) {
        throw new ApiError(
          "unexpected",
          "The backend returned an invalid mission event stream.",
          { status: response.status, requestId },
        );
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let terminal = false;
      while (!signal.aborted) {
        const chunk = await reader.read();
        buffer += decoder.decode(chunk.value, { stream: !chunk.done });
        const records = buffer.split(/\r?\n\r?\n/);
        buffer = records.pop() ?? "";
        for (const record of records) {
          const data = record
            .split(/\r?\n/)
            .filter((line) => line.startsWith("data:"))
            .map((line) => line.slice(5).trimStart())
            .join("\n");
          if (data === "") continue;
          let event: AgentRunEvent;
          try {
            event = parseAgentRunEvent(JSON.parse(data));
          } catch {
            throw new ApiError(
              "unexpected",
              "The backend returned an invalid mission event.",
              { status: response.status, requestId },
            );
          }
          if (event.sequence <= sequence) continue;
          sequence = event.sequence;
          onEvent(event);
          terminal = ["completed", "failed", "cancelled", "timed_out"].includes(
            event.status,
          );
        }
        if (chunk.done || terminal) break;
      }
      await reader.cancel().catch(() => undefined);
      if (terminal || signal.aborted) return;
      await new Promise<void>((resolve) => {
        const onAbort = () => {
          globalThis.clearTimeout(timer);
          resolve();
        };
        const timer = globalThis.setTimeout(() => {
          signal.removeEventListener("abort", onAbort);
          resolve();
        }, 250);
        signal.addEventListener("abort", onAbort, { once: true });
      });
    }
  }

  getSystemDiagnostics(signal?: AbortSignal): Promise<SystemDiagnostics> {
    return this.#request("api/v1/diagnostics", {
      signal,
      decode: parseSystemDiagnostics,
    });
  }

  getExternalAISettings(signal?: AbortSignal): Promise<ExternalAISettings> {
    return this.#request("api/v1/external-ai/settings", {
      signal,
      decode: parseExternalAISettings,
    });
  }

  updateExternalAIEnabled(
    enabled: boolean,
    signal?: AbortSignal,
  ): Promise<ExternalAISettings> {
    return this.#request("api/v1/external-ai/settings", {
      method: "PUT",
      body: { enabled },
      signal,
      decode: parseExternalAISettings,
    });
  }

  upsertExternalAIProvider(
    providerId: string,
    request: ExternalProviderUpsertRequest,
    signal?: AbortSignal,
  ): Promise<ExternalAISettings> {
    return this.#request(
      `api/v1/external-ai/providers/${encodeURIComponent(providerId)}`,
      {
        method: "PUT",
        body: request,
        signal,
        decode: parseExternalAISettings,
      },
    );
  }

  deleteExternalAIProvider(
    providerId: string,
    signal?: AbortSignal,
  ): Promise<ExternalAISettings> {
    return this.#request(
      `api/v1/external-ai/providers/${encodeURIComponent(providerId)}`,
      {
        method: "DELETE",
        signal,
        decode: parseExternalAISettings,
      },
    );
  }

  getSelfUpdateStatus(signal?: AbortSignal): Promise<SelfUpdateStatus> {
    return this.#request("api/v1/updates/status", {
      signal,
      decode: parseSelfUpdateStatus,
    });
  }

  decideSelfUpdate(
    decision: "update" | "cancel",
    signal?: AbortSignal,
  ): Promise<SelfUpdateStatus> {
    return this.#request("api/v1/updates/decision", {
      method: "POST",
      body: { decision },
      signal,
      decode: parseSelfUpdateStatus,
    });
  }

  async #request<T>(path: string, options: RequestOptions<T>): Promise<T> {
    const url = new URL(path, `${this.#baseUrl}/`);
    const headers = new Headers({
      Accept: "application/json",
      Authorization: `Bearer ${this.#token}`,
    });
    if (options.body !== undefined) headers.set("Content-Type", "application/json");
    for (const [name, value] of Object.entries(options.headers ?? {})) {
      headers.set(name, value);
    }

    let response: Response;
    try {
      response = await this.#fetch.call(globalThis, url, {
        method: options.method ?? "GET",
        headers,
        body:
          options.body === undefined ? undefined : JSON.stringify(options.body),
        signal: options.signal,
      });
    } catch (error) {
      if (
        options.signal?.aborted === true ||
        (error instanceof DOMException && error.name === "AbortError")
      ) {
        throw new ApiError("cancelled", "Request cancelled.");
      }
      throw new ApiError("network", "Could not reach the local backend.");
    }

    const requestId = response.headers.get("X-Request-ID");
    if (!response.ok) {
      const code = await readSafeErrorCode(response);
      if (response.status === 401) this.#onUnauthorized?.();
      throw errorForStatus(response.status, requestId, code);
    }

    try {
      return options.decode(await response.json());
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw new ApiError(
        "unexpected",
        "The backend returned an invalid response.",
        { status: response.status, requestId },
      );
    }
  }

  async #requestNoContent(
    path: string,
    options: { method: "DELETE"; signal?: AbortSignal },
  ): Promise<void> {
    const url = new URL(path, `${this.#baseUrl}/`);
    const headers = new Headers({
      Accept: "application/json",
      Authorization: `Bearer ${this.#token}`,
    });
    let response: Response;
    try {
      response = await this.#fetch.call(globalThis, url, {
        method: options.method,
        headers,
        signal: options.signal,
      });
    } catch (error) {
      if (
        options.signal?.aborted === true ||
        (error instanceof DOMException && error.name === "AbortError")
      ) {
        throw new ApiError("cancelled", "Request cancelled.");
      }
      throw new ApiError("network", "Could not reach the local backend.");
    }

    const requestId = response.headers.get("X-Request-ID");
    if (!response.ok) {
      const code = await readSafeErrorCode(response);
      if (response.status === 401) this.#onUnauthorized?.();
      throw errorForStatus(response.status, requestId, code);
    }
    if (response.status !== 204) {
      throw new ApiError(
        "unexpected",
        "The backend returned an invalid response.",
        { status: response.status, requestId },
      );
    }
  }

  getCurrentUser(signal?: AbortSignal): Promise<CurrentUser> {
    return this.#request("api/v1/users/me", {
      signal,
      decode: parseCurrentUser,
    });
  }

  rotateAccessToken(signal?: AbortSignal): Promise<AccessTokenRotation> {
    return this.#request("api/v1/users/me/access-token/rotate", {
      method: "POST",
      body: {},
      signal,
      decode: parseAccessTokenRotation,
    });
  }

  listUserSessions(signal?: AbortSignal): Promise<UserSessionPage> {
    return this.#request("api/v1/users/me/sessions", {
      signal,
      decode: parseUserSessionPage,
    });
  }

  createUserSession(
    request: UserSessionCreateRequest,
    signal?: AbortSignal,
  ): Promise<UserSessionProvision> {
    return this.#request("api/v1/users/me/sessions", {
      method: "POST",
      body: request,
      signal,
      decode: parseUserSessionProvision,
    });
  }

  renameCurrentUserSession(
    request: UserSessionUpdateRequest,
    signal?: AbortSignal,
  ): Promise<UserSession> {
    return this.#request("api/v1/users/me/sessions/current", {
      method: "PATCH",
      body: request,
      signal,
      decode: parseUserSession,
    });
  }

  revokeCurrentUserSession(signal?: AbortSignal): Promise<void> {
    return this.#requestNoContent("api/v1/users/me/sessions/current", {
      method: "DELETE",
      signal,
    });
  }

  revokeUserSession(sessionId: string, signal?: AbortSignal): Promise<void> {
    return this.#requestNoContent(
      `api/v1/users/me/sessions/${encodeURIComponent(sessionId)}`,
      { method: "DELETE", signal },
    );
  }

  listModels(signal?: AbortSignal): Promise<LocalModelPage> {
    return this.#request("api/v1/ai/models", {
      signal,
      decode: parseModelPage,
    });
  }

  listMemories(
    options: { includeDeleted?: boolean; signal?: AbortSignal } = {},
  ): Promise<MemoryPage> {
    const query = new URLSearchParams();
    if (options.includeDeleted !== undefined) {
      query.set("include_deleted", String(options.includeDeleted));
    }
    const suffix = query.size === 0 ? "" : `?${query.toString()}`;
    return this.#request(`api/v1/memories${suffix}`, {
      signal: options.signal,
      decode: parseMemoryPage,
    });
  }

  createMemory(
    request: MemoryCreateRequest,
    signal?: AbortSignal,
  ): Promise<PersonalMemory> {
    return this.#request("api/v1/memories", {
      method: "POST",
      body: request,
      signal,
      decode: parsePersonalMemory,
    });
  }

  forgetMemory(memoryId: string, signal?: AbortSignal): Promise<PersonalMemory> {
    return this.#request(
      `api/v1/memories/${encodeURIComponent(memoryId)}`,
      { method: "DELETE", signal, decode: parsePersonalMemory },
    );
  }

  getMemorySetting(signal?: AbortSignal): Promise<MemorySetting> {
    return this.#request("api/v1/memories/settings", {
      signal,
      decode: parseMemorySetting,
    });
  }

  updateMemorySetting(
    enabled: boolean,
    signal?: AbortSignal,
  ): Promise<MemorySetting> {
    return this.#request("api/v1/memories/settings", {
      method: "PUT",
      body: { enabled },
      signal,
      decode: parseMemorySetting,
    });
  }


  listTools(signal?: AbortSignal): Promise<ToolDescriptorPage> {
    return this.#request("api/v1/tools", {
      signal,
      decode: parseToolDescriptorPage,
    });
  }

  listToolExecutions(
    options: { limit?: number; signal?: AbortSignal } = {},
  ): Promise<ToolExecutionPage> {
    const query = new URLSearchParams();
    if (options.limit !== undefined) query.set("limit", String(options.limit));
    const suffix = query.size === 0 ? "" : `?${query.toString()}`;
    return this.#request(`api/v1/tools/executions${suffix}`, {
      signal: options.signal,
      decode: parseToolExecutionPage,
    });
  }

  executeTool(
    toolName: string,
    request: ToolExecutionRequest,
    signal?: AbortSignal,
  ): Promise<ToolExecution> {
    return this.#request(
      `api/v1/tools/${encodeURIComponent(toolName)}/executions`,
      {
        method: "POST",
        body: request,
        signal,
        decode: parseToolExecution,
      },
    );
  }


  listWorkflows(
    options: { limit?: number; signal?: AbortSignal } = {},
  ): Promise<WorkflowPage> {
    const query = new URLSearchParams();
    if (options.limit !== undefined) query.set("limit", String(options.limit));
    const suffix = query.size === 0 ? "" : `?${query.toString()}`;
    return this.#request(`api/v1/workflows${suffix}`, {
      signal: options.signal,
      decode: parseWorkflowPage,
    });
  }

  createWorkflow(
    request: WorkflowCreateRequest,
    signal?: AbortSignal,
  ): Promise<Workflow> {
    return this.#request("api/v1/workflows", {
      method: "POST",
      body: request,
      signal,
      decode: parseWorkflow,
    });
  }

  getWorkflow(workflowId: string, signal?: AbortSignal): Promise<Workflow> {
    return this.#request(
      `api/v1/workflows/${encodeURIComponent(workflowId)}`,
      { signal, decode: parseWorkflow },
    );
  }

  startWorkflow(workflowId: string, signal?: AbortSignal): Promise<Workflow> {
    return this.#request(
      `api/v1/workflows/${encodeURIComponent(workflowId)}/start`,
      { method: "POST", signal, decode: parseWorkflow },
    );
  }

  cancelWorkflow(workflowId: string, signal?: AbortSignal): Promise<Workflow> {
    return this.#request(
      `api/v1/workflows/${encodeURIComponent(workflowId)}`,
      { method: "DELETE", signal, decode: parseWorkflow },
    );
  }

  listConversations(
    options: {
      limit?: number;
      cursor?: ConversationCursor;
      includeArchived?: boolean;
      signal?: AbortSignal;
    } = {},
  ): Promise<ConversationPage> {
    const query = new URLSearchParams();
    if (options.limit !== undefined) query.set("limit", String(options.limit));
    if (options.cursor !== undefined) {
      query.set("cursor_updated_at", options.cursor.updated_at);
      query.set("cursor_id", options.cursor.id);
    }
    if (options.includeArchived !== undefined) {
      query.set("include_archived", String(options.includeArchived));
    }
    const suffix = query.size === 0 ? "" : `?${query.toString()}`;
    return this.#request(`api/v1/conversations${suffix}`, {
      signal: options.signal,
      decode: parseConversationPage,
    });
  }

  searchConversations(
    request: ConversationSearchRequest,
    signal?: AbortSignal,
  ): Promise<ConversationPage> {
    return this.#request("api/v1/conversations/search", {
      method: "POST",
      body: request,
      signal,
      decode: parseConversationPage,
    });
  }

  createConversation(
    request: ConversationCreateRequest,
    signal?: AbortSignal,
  ): Promise<ConversationCreateResponse> {
    return this.#request("api/v1/conversations", {
      method: "POST",
      body: request,
      signal,
      decode: parseConversationCreateResponse,
    });
  }

  getConversation(
    conversationId: string,
    signal?: AbortSignal,
  ): Promise<ConversationSummary> {
    return this.#request(
      `api/v1/conversations/${encodeURIComponent(conversationId)}`,
      { signal, decode: parseConversation },
    );
  }

  renameConversation(
    conversationId: string,
    request: ConversationRenameRequest,
    signal?: AbortSignal,
  ): Promise<ConversationSummary> {
    return this.#request(
      `api/v1/conversations/${encodeURIComponent(conversationId)}`,
      { method: "PATCH", body: request, signal, decode: parseConversation },
    );
  }

  updateConversationState(
    conversationId: string,
    request: ConversationStateUpdateRequest,
    signal?: AbortSignal,
  ): Promise<ConversationSummary> {
    return this.#request(
      `api/v1/conversations/${encodeURIComponent(conversationId)}/state`,
      { method: "PATCH", body: request, signal, decode: parseConversation },
    );
  }

  forkConversation(
    conversationId: string,
    request: ConversationForkRequest = {},
    signal?: AbortSignal,
  ): Promise<ConversationSummary> {
    return this.#request(
      `api/v1/conversations/${encodeURIComponent(conversationId)}/fork`,
      { method: "POST", body: request, signal, decode: parseConversation },
    );
  }

  deleteConversation(
    conversationId: string,
    signal?: AbortSignal,
  ): Promise<void> {
    return this.#requestNoContent(
      `api/v1/conversations/${encodeURIComponent(conversationId)}`,
      { method: "DELETE", signal },
    );
  }

  listMessages(
    conversationId: string,
    options: { limit?: number; cursor?: number; signal?: AbortSignal } = {},
  ): Promise<MessagePage> {
    const query = new URLSearchParams();
    if (options.limit !== undefined) query.set("limit", String(options.limit));
    if (options.cursor !== undefined) query.set("cursor", String(options.cursor));
    const suffix = query.size === 0 ? "" : `?${query.toString()}`;
    return this.#request(
      `api/v1/conversations/${encodeURIComponent(conversationId)}/messages${suffix}`,
      { signal: options.signal, decode: parseMessagePage },
    );
  }

  ingestDocument(
    assetId: string,
    signal?: AbortSignal,
  ): Promise<IndexedDocument> {
    return this.#request(
      `api/v1/documents/assets/${encodeURIComponent(assetId)}/ingest`,
      {
        method: "POST",
        signal,
        decode: parseIndexedDocument,
      },
    );
  }

  getDocument(
    documentId: string,
    signal?: AbortSignal,
  ): Promise<IndexedDocument> {
    return this.#request(
      `api/v1/documents/${encodeURIComponent(documentId)}`,
      { signal, decode: parseIndexedDocument },
    );
  }

  uploadAsset(
    file: File,
    idempotencyKey: string,
    options: { signal?: AbortSignal; onProgress?: (value: UploadProgress) => void } = {},
  ): Promise<Asset> {
    return new Promise<Asset>((resolve, reject) => {
      const xhr = this.#xhrFactory();
      let settled = false;
      const abortRequest = () => xhr.abort();
      const cleanup = () => {
        options.signal?.removeEventListener("abort", abortRequest);
      };
      const finish = (action: () => void) => {
        if (settled) return;
        settled = true;
        cleanup();
        action();
      };

      if (options.signal?.aborted === true) {
        reject(new ApiError("cancelled", "Request cancelled."));
        return;
      }
      options.signal?.addEventListener("abort", abortRequest, { once: true });

      xhr.open(
        "POST",
        new URL("api/v1/assets", `${this.#baseUrl}/`).toString(),
      );
      xhr.responseType = "json";
      xhr.setRequestHeader("Accept", "application/json");
      xhr.setRequestHeader("Authorization", `Bearer ${this.#token}`);
      xhr.setRequestHeader("Idempotency-Key", idempotencyKey);
      xhr.upload.onprogress = (event) => {
        options.onProgress?.({
          loaded: event.loaded,
          total: event.lengthComputable ? event.total : null,
        });
      };
      xhr.onload = () => {
        const requestId = xhr.getResponseHeader("X-Request-ID");
        if (xhr.status < 200 || xhr.status >= 300) {
          const code = safeEnvelopeCode(xhr.response);
          if (xhr.status === 401) this.#onUnauthorized?.();
          finish(() => reject(attachmentErrorForStatus(xhr.status, requestId, code)));
          return;
        }
        try {
          const asset = parseAsset(xhr.response);
          finish(() => resolve(asset));
        } catch {
          finish(() =>
            reject(
              new ApiError(
                "unexpected",
                "The backend returned an invalid response.",
                { status: xhr.status, requestId },
              ),
            ),
          );
        }
      };
      xhr.onerror = () => {
        finish(() => reject(new ApiError("network", "Could not reach the local backend.")));
      };
      xhr.onabort = () => {
        finish(() => reject(new ApiError("cancelled", "Request cancelled.")));
      };

      const form = new FormData();
      form.append("file", file);
      xhr.send(form);
    });
  }

  async downloadAsset(
    assetId: string,
    signal?: AbortSignal,
  ): Promise<Blob> {
    const url = new URL(`api/v1/assets/${encodeURIComponent(assetId)}/content`, `${this.#baseUrl}/`);
    const headers = new Headers({
      Accept: "application/octet-stream",
      Authorization: `Bearer ${this.#token}`,
    });
    let response: Response;
    try {
      response = await this.#fetch(url, { headers, signal });
    } catch (error) {
      if (
        signal?.aborted === true ||
        (error instanceof DOMException && error.name === "AbortError")
      ) {
        throw new ApiError("cancelled", "Request cancelled.");
      }
      throw new ApiError("network", "Could not reach the local backend.");
    }
    const requestId = response.headers.get("X-Request-ID");
    if (!response.ok) {
      const code = await readSafeErrorCode(response);
      if (response.status === 401) this.#onUnauthorized?.();
      throw attachmentErrorForStatus(response.status, requestId, code);
    }
    try {
      const blob = await response.blob();
      const mediaType = response.headers.get("X-Asset-Media-Type");
      if (mediaType === null || !/^[a-z0-9][a-z0-9!#$&^_.+-]*\/[a-z0-9][a-z0-9!#$&^_.+-]*$/.test(mediaType)) {
        throw new Error("invalid asset media type");
      }
      return new Blob([blob], { type: mediaType });
    } catch {
      throw new ApiError(
        "unexpected",
        "The backend returned an invalid response.",
        { status: response.status, requestId },
      );
    }
  }

  async deleteAsset(assetId: string, signal?: AbortSignal): Promise<void> {
    const url = new URL(`api/v1/assets/${encodeURIComponent(assetId)}`, `${this.#baseUrl}/`);
    const headers = new Headers({
      Accept: "application/json",
      Authorization: `Bearer ${this.#token}`,
    });
    let response: Response;
    try {
      response = await this.#fetch(url, { method: "DELETE", headers, signal });
    } catch (error) {
      if (
        signal?.aborted === true ||
        (error instanceof DOMException && error.name === "AbortError")
      ) {
        throw new ApiError("cancelled", "Request cancelled.");
      }
      throw new ApiError("network", "Could not reach the local backend.");
    }
    const requestId = response.headers.get("X-Request-ID");
    if (!response.ok) {
      const code = await readSafeErrorCode(response);
      if (response.status === 401) this.#onUnauthorized?.();
      throw attachmentErrorForStatus(response.status, requestId, code);
    }
  }

  generateResponse(
    conversationId: string,
    request: ConversationTextGenerationRequest,
    signal?: AbortSignal,
  ): Promise<ConversationTextGenerationResponse> {
    return this.#request(
      `api/v1/conversations/${encodeURIComponent(conversationId)}/messages/generate`,
      {
        method: "POST",
        body: request,
        signal,
        decode: parseGenerationResponse,
      },
    );
  }

  transcribeVoice(
    request: VoiceTranscriptionRequest,
    signal?: AbortSignal,
  ): Promise<VoiceTranscription> {
    return this.#request("api/v1/voice/transcriptions", {
      method: "POST",
      body: request,
      signal,
      decode: parseVoiceTranscription,
    });
  }

  synthesizeVoice(
    request: VoiceSynthesisRequest,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<VoiceSynthesis> {
    return this.#request("api/v1/voice/syntheses", {
      method: "POST",
      body: request,
      headers: { "Idempotency-Key": idempotencyKey },
      signal,
      decode: parseVoiceSynthesis,
    });
  }

  generateImage(
    request: ImageGenerationRequest,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<ImageOperation> {
    return this.#request("api/v1/images/generations", {
      method: "POST",
      body: request,
      headers: { "Idempotency-Key": idempotencyKey },
      signal,
      decode: parseImageOperation,
    });
  }

  editImage(
    request: ImageEditingRequest,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<ImageOperation> {
    return this.#request("api/v1/images/edits", {
      method: "POST",
      body: request,
      headers: { "Idempotency-Key": idempotencyKey },
      signal,
      decode: parseImageOperation,
    });
  }
}
