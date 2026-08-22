import {
  type BackendErrorEnvelope,
  type Asset,
  type ConversationCreateRequest,
  type ConversationCreateResponse,
  type ConversationCursor,
  type ConversationPage,
  type ConversationSummary,
  type ConversationTextGenerationRequest,
  type ConversationTextGenerationResponse,
  type CurrentUser,
  type IndexedDocument,
  type LocalModelPage,
  type MemoryCreateRequest,
  type MemoryPage,
  type MemorySetting,
  type MessagePage,
  type PersonalMemory,
  type ProductCapabilityPage,
  type ToolDescriptorPage,
  type ToolExecution,
  type ToolExecutionPage,
  type ToolExecutionRequest,
  type Workflow,
  type WorkflowCreateRequest,
  type WorkflowPage,
  parseIndexedDocument,
  parseAsset,
  parseConversation,
  parseConversationCreateResponse,
  parseConversationPage,
  parseCurrentUser,
  parseGenerationResponse,
  parseMemoryPage,
  parseMemorySetting,
  parseMessagePage,
  parseModelPage,
  parsePersonalMemory,
  parseProductCapabilityPage,
  parseToolDescriptorPage,
  parseToolExecution,
  parseToolExecutionPage,
  parseWorkflow,
  parseWorkflowPage,
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

export function normalizeApiBaseUrl(value: string | undefined): string {
  const candidate = value?.trim() || "http://127.0.0.1:8000";
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
  return parsed.origin;
}

export const API_BASE_URL = normalizeApiBaseUrl(import.meta.env.VITE_API_BASE_URL);

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
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
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

  async #request<T>(path: string, options: RequestOptions<T>): Promise<T> {
    const url = new URL(path, `${this.#baseUrl}/`);
    const headers = new Headers({
      Accept: "application/json",
      Authorization: `Bearer ${this.#token}`,
    });
    if (options.body !== undefined) headers.set("Content-Type", "application/json");

    let response: Response;
    try {
      response = await this.#fetch(url, {
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

  getCurrentUser(signal?: AbortSignal): Promise<CurrentUser> {
    return this.#request("api/v1/users/me", {
      signal,
      decode: parseCurrentUser,
    });
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
      signal?: AbortSignal;
    } = {},
  ): Promise<ConversationPage> {
    const query = new URLSearchParams();
    if (options.limit !== undefined) query.set("limit", String(options.limit));
    if (options.cursor !== undefined) {
      query.set("cursor_updated_at", options.cursor.updated_at);
      query.set("cursor_id", options.cursor.id);
    }
    const suffix = query.size === 0 ? "" : `?${query.toString()}`;
    return this.#request(`api/v1/conversations${suffix}`, {
      signal: options.signal,
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
      return await response.blob();
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
}
