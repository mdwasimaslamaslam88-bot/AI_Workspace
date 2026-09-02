import {
  type Asset,
  type AccessTokenRotation,
  type AgentOSCapabilities,
  type AgentRun,
  type AgentRunCreateRequest,
  type AgentRunPage,
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
  type Connector,
  type ConnectorExecutionPage,
  type ConnectorExecutionRequest,
  type ConnectorExecutionResult,
  type ConnectorPage,
  type ConnectorSettings,
  type ConnectorWriteRequest,
  type CurrentUser,
  type ExternalAISettings,
  type ExternalProviderUpsertRequest,
  type FeatureRegistry,
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
  type ToolDescriptorPage,
  type ToolExecution,
  type ToolExecutionPage,
  type ToolExecutionRequest,
  type UserSession,
  type UserSessionCreateRequest,
  type UserSessionPage,
  type UserSessionProvision,
  type UserSessionUpdateRequest,
  type VoiceSynthesis,
  type VoiceSynthesisRequest,
  type VoiceTranscription,
  type Workflow,
  type WorkflowCreateRequest,
  type WorkflowPage,
  parseAsset,
  parseAccessTokenRotation,
  parseAgentOSCapabilities,
  parseAgentRun,
  parseAgentRunPage,
  parseConversation,
  parseConversationCreateResponse,
  parseConversationPage,
  parseConnector,
  parseConnectorExecutionPage,
  parseConnectorExecutionResult,
  parseConnectorPage,
  parseConnectorSettings,
  parseCurrentUser,
  parseExternalAISettings,
  parseFeatureRegistry,
  parseGenerationResponse,
  parseImageOperation,
  parseMemoryPage,
  parseMemorySetting,
  parseMessagePage,
  parseModelPage,
  parsePersonalMemory,
  parseProductCapabilityPage,
  parseSelfUpdateStatus,
  parseSystemDiagnostics,
  parseToolDescriptorPage,
  parseToolExecution,
  parseToolExecutionPage,
  parseUserSession,
  parseUserSessionPage,
  parseUserSessionProvision,
  parseVoiceSynthesis,
  parseVoiceTranscription,
  parseWorkflow,
  parseWorkflowPage,
} from "@work-station/shared";

export type MobileApiErrorKind =
  | "authentication"
  | "network"
  | "unavailable"
  | "validation"
  | "conflict"
  | "cancelled"
  | "server";

export class MobileApiError extends Error {
  constructor(
    readonly kind: MobileApiErrorKind,
    message: string,
    readonly status: number | null = null,
    readonly requestId: string | null = null,
  ) {
    super(message);
    this.name = "MobileApiError";
  }
}

export interface MobileUpload {
  uri: string;
  name: string;
  mimeType: string;
}

export interface PrivateAssetContent {
  bytes: Uint8Array;
  mediaType: string;
}

const MAX_MOBILE_MEDIA_BYTES = 64 * 1024 * 1024;
const mediaTypePattern = /^[a-z0-9][a-z0-9!#$&^_.+-]*\/[a-z0-9][a-z0-9!#$&^_.+-]*$/;

export function createIdempotencyKey(): string {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  const bytes = new Uint8Array(16);
  if (typeof globalThis.crypto?.getRandomValues === "function") {
    globalThis.crypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }
  bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40;
  bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function normalizeMobileApiBaseUrl(value: string | undefined): string {
  const candidate = value?.trim() || "http://127.0.0.1:8000";
  let parsed: URL;
  try {
    parsed = new URL(candidate);
  } catch {
    throw new Error("EXPO_PUBLIC_API_BASE_URL must be a valid HTTP origin.");
  }
  if (
    !["http:", "https:"].includes(parsed.protocol) ||
    parsed.username !== "" ||
    parsed.password !== "" ||
    parsed.pathname !== "/" ||
    parsed.search !== "" ||
    parsed.hash !== ""
  ) {
    throw new Error("EXPO_PUBLIC_API_BASE_URL must be an HTTP origin without credentials or a path.");
  }
  if (
    parsed.protocol === "http:" &&
    !["localhost", "127.0.0.1", "[::1]"].includes(parsed.hostname)
  ) {
    throw new Error("Remote EXPO_PUBLIC_API_BASE_URL origins must use HTTPS.");
  }
  return parsed.origin;
}

export const MOBILE_API_BASE_URL = normalizeMobileApiBaseUrl(
  process.env.EXPO_PUBLIC_API_BASE_URL,
);

type Decoder<T> = (value: unknown) => T;

function statusError(status: number, requestId: string | null): MobileApiError {
  if (status === 401 || status === 403) {
    return new MobileApiError("authentication", "Authentication is required.", status, requestId);
  }
  if (status === 409) {
    return new MobileApiError("conflict", "The conversation changed. Refresh and retry.", status, requestId);
  }
  if (status === 400 || status === 413 || status === 422) {
    return new MobileApiError("validation", "The request could not be accepted.", status, requestId);
  }
  if (status === 429 || status === 503 || status === 504) {
    return new MobileApiError("unavailable", "The Personal AI is temporarily unavailable.", status, requestId);
  }
  return new MobileApiError("server", "The backend could not complete the request.", status, requestId);
}

export class MobileApiClient {
  readonly #baseUrl: string;
  readonly #token: string;
  readonly #fetch: typeof fetch;

  constructor(
    token: string,
    options: { baseUrl?: string; fetchImplementation?: typeof fetch } = {},
  ) {
    if (token.length === 0) throw new Error("A bearer token is required.");
    this.#token = token;
    this.#baseUrl = normalizeMobileApiBaseUrl(options.baseUrl ?? MOBILE_API_BASE_URL);
    this.#fetch = options.fetchImplementation ?? fetch;
  }

  async #request<T>(
    path: string,
    decoder: Decoder<T>,
    options: {
      method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
      body?: unknown;
      headers?: Record<string, string>;
      signal?: AbortSignal;
    } = {},
  ): Promise<T> {
    let response: Response;
    try {
      response = await this.#fetch(new URL(path, `${this.#baseUrl}/`), {
        method: options.method ?? "GET",
        headers: {
          Accept: "application/json",
          Authorization: `Bearer ${this.#token}`,
          ...(options.body === undefined ? {} : { "Content-Type": "application/json" }),
          ...options.headers,
        },
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
        signal: options.signal,
      });
    } catch (error) {
      if (options.signal?.aborted || (error instanceof Error && error.name === "AbortError")) {
        throw new MobileApiError("cancelled", "Request cancelled.");
      }
      throw new MobileApiError("network", "Could not reach WORK STATION.");
    }
    const requestId = response.headers.get("X-Request-ID");
    if (!response.ok) throw statusError(response.status, requestId);
    try {
      return decoder(response.status === 204 ? null : await response.json());
    } catch {
      throw new MobileApiError("server", "The backend returned an invalid response.", response.status, requestId);
    }
  }

  getCurrentUser(signal?: AbortSignal): Promise<CurrentUser> {
    return this.#request("api/v1/users/me", parseCurrentUser, { signal });
  }

  getAgentOSCapabilities(signal?: AbortSignal): Promise<AgentOSCapabilities> {
    return this.#request("api/v1/agent-os/capabilities", parseAgentOSCapabilities, { signal });
  }

  listAgentRuns(signal?: AbortSignal): Promise<AgentRunPage> {
    return this.#request("api/v1/agent-os/runs?limit=20", parseAgentRunPage, { signal });
  }

  createAgentRun(request: AgentRunCreateRequest, signal?: AbortSignal): Promise<AgentRun> {
    return this.#request("api/v1/agent-os/runs", parseAgentRun, {
      method: "POST",
      body: request,
      signal,
    });
  }

  cancelAgentRun(runId: string, signal?: AbortSignal): Promise<AgentRun> {
    return this.#request(
      `api/v1/agent-os/runs/${encodeURIComponent(runId)}/cancel`,
      parseAgentRun,
      { method: "POST", signal },
    );
  }

  listModels(signal?: AbortSignal): Promise<LocalModelPage> {
    return this.#request("api/v1/ai/models", parseModelPage, { signal });
  }

  getCapabilities(signal?: AbortSignal): Promise<ProductCapabilityPage> {
    return this.#request("api/v1/ai/capabilities", parseProductCapabilityPage, { signal });
  }

  getFeatureRegistry(signal?: AbortSignal): Promise<FeatureRegistry> {
    return this.#request("api/v1/features", parseFeatureRegistry, { signal });
  }

  getSystemDiagnostics(signal?: AbortSignal): Promise<SystemDiagnostics> {
    return this.#request("api/v1/diagnostics", parseSystemDiagnostics, { signal });
  }

  getExternalAISettings(signal?: AbortSignal): Promise<ExternalAISettings> {
    return this.#request("api/v1/external-ai/settings", parseExternalAISettings, { signal });
  }

  getConnectorSettings(signal?: AbortSignal): Promise<ConnectorSettings> {
    return this.#request("api/v1/connectors/settings", parseConnectorSettings, { signal });
  }

  listConnectors(signal?: AbortSignal): Promise<ConnectorPage> {
    return this.#request("api/v1/connectors", parseConnectorPage, { signal });
  }

  createConnector(request: ConnectorWriteRequest, signal?: AbortSignal): Promise<Connector> {
    return this.#request("api/v1/connectors", parseConnector, {
      method: "POST", body: request, signal,
    });
  }

  revokeConnector(connectorId: string, signal?: AbortSignal): Promise<Connector> {
    return this.#request(
      `api/v1/connectors/${encodeURIComponent(connectorId)}`,
      parseConnector,
      { method: "DELETE", signal },
    );
  }

  checkConnectorHealth(
    connectorId: string,
    signal?: AbortSignal,
  ): Promise<ConnectorExecutionResult> {
    return this.#request(
      `api/v1/connectors/${encodeURIComponent(connectorId)}/health`,
      parseConnectorExecutionResult,
      { method: "POST", signal },
    );
  }

  executeConnector(
    connectorId: string,
    request: ConnectorExecutionRequest,
    signal?: AbortSignal,
  ): Promise<ConnectorExecutionResult> {
    return this.#request(
      `api/v1/connectors/${encodeURIComponent(connectorId)}/executions`,
      parseConnectorExecutionResult,
      { method: "POST", body: request, signal },
    );
  }

  listConnectorExecutions(
    options: { connectorId?: string; limit?: number; signal?: AbortSignal } = {},
  ): Promise<ConnectorExecutionPage> {
    const query = new URLSearchParams();
    if (options.connectorId !== undefined) query.set("connector_id", options.connectorId);
    if (options.limit !== undefined) query.set("limit", String(options.limit));
    const suffix = query.size === 0 ? "" : `?${query.toString()}`;
    return this.#request(
      `api/v1/connectors/executions${suffix}`,
      parseConnectorExecutionPage,
      { signal: options.signal },
    );
  }

  updateExternalAIEnabled(enabled: boolean, signal?: AbortSignal): Promise<ExternalAISettings> {
    return this.#request("api/v1/external-ai/settings", parseExternalAISettings, {
      method: "PUT",
      body: { enabled },
      signal,
    });
  }

  upsertExternalAIProvider(
    providerId: string,
    request: ExternalProviderUpsertRequest,
    signal?: AbortSignal,
  ): Promise<ExternalAISettings> {
    return this.#request(
      `api/v1/external-ai/providers/${encodeURIComponent(providerId)}`,
      parseExternalAISettings,
      { method: "PUT", body: request, signal },
    );
  }

  getSelfUpdateStatus(signal?: AbortSignal): Promise<SelfUpdateStatus> {
    return this.#request("api/v1/updates/status", parseSelfUpdateStatus, { signal });
  }

  decideSelfUpdate(
    decision: "update" | "cancel",
    signal?: AbortSignal,
  ): Promise<SelfUpdateStatus> {
    return this.#request("api/v1/updates/decision", parseSelfUpdateStatus, {
      method: "POST",
      body: { decision },
      signal,
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
    return this.#request(`api/v1/memories${suffix}`, parseMemoryPage, {
      signal: options.signal,
    });
  }

  createMemory(request: MemoryCreateRequest, signal?: AbortSignal): Promise<PersonalMemory> {
    return this.#request("api/v1/memories", parsePersonalMemory, {
      method: "POST",
      body: request,
      signal,
    });
  }

  forgetMemory(memoryId: string, signal?: AbortSignal): Promise<PersonalMemory> {
    return this.#request(
      `api/v1/memories/${encodeURIComponent(memoryId)}`,
      parsePersonalMemory,
      { method: "DELETE", signal },
    );
  }

  getMemorySetting(signal?: AbortSignal): Promise<MemorySetting> {
    return this.#request("api/v1/memories/settings", parseMemorySetting, { signal });
  }

  updateMemorySetting(enabled: boolean, signal?: AbortSignal): Promise<MemorySetting> {
    return this.#request("api/v1/memories/settings", parseMemorySetting, {
      method: "PUT",
      body: { enabled },
      signal,
    });
  }

  listTools(signal?: AbortSignal): Promise<ToolDescriptorPage> {
    return this.#request("api/v1/tools", parseToolDescriptorPage, { signal });
  }

  listToolExecutions(
    options: { limit?: number; signal?: AbortSignal } = {},
  ): Promise<ToolExecutionPage> {
    const query = new URLSearchParams();
    if (options.limit !== undefined) query.set("limit", String(options.limit));
    const suffix = query.size === 0 ? "" : `?${query.toString()}`;
    return this.#request(`api/v1/tools/executions${suffix}`, parseToolExecutionPage, {
      signal: options.signal,
    });
  }

  executeTool(
    toolName: string,
    request: ToolExecutionRequest,
    signal?: AbortSignal,
  ): Promise<ToolExecution> {
    return this.#request(
      `api/v1/tools/${encodeURIComponent(toolName)}/executions`,
      parseToolExecution,
      { method: "POST", body: request, signal },
    );
  }

  listWorkflows(
    options: { limit?: number; signal?: AbortSignal } = {},
  ): Promise<WorkflowPage> {
    const query = new URLSearchParams();
    if (options.limit !== undefined) query.set("limit", String(options.limit));
    const suffix = query.size === 0 ? "" : `?${query.toString()}`;
    return this.#request(`api/v1/workflows${suffix}`, parseWorkflowPage, {
      signal: options.signal,
    });
  }

  createWorkflow(request: WorkflowCreateRequest, signal?: AbortSignal): Promise<Workflow> {
    return this.#request("api/v1/workflows", parseWorkflow, {
      method: "POST",
      body: request,
      signal,
    });
  }

  getWorkflow(workflowId: string, signal?: AbortSignal): Promise<Workflow> {
    return this.#request(
      `api/v1/workflows/${encodeURIComponent(workflowId)}`,
      parseWorkflow,
      { signal },
    );
  }

  startWorkflow(workflowId: string, signal?: AbortSignal): Promise<Workflow> {
    return this.#request(
      `api/v1/workflows/${encodeURIComponent(workflowId)}/start`,
      parseWorkflow,
      { method: "POST", signal },
    );
  }

  cancelWorkflow(workflowId: string, signal?: AbortSignal): Promise<Workflow> {
    return this.#request(
      `api/v1/workflows/${encodeURIComponent(workflowId)}`,
      parseWorkflow,
      { method: "DELETE", signal },
    );
  }

  rotateAccessToken(signal?: AbortSignal): Promise<AccessTokenRotation> {
    return this.#request(
      "api/v1/users/me/access-token/rotate",
      parseAccessTokenRotation,
      { method: "POST", body: {}, signal },
    );
  }

  listUserSessions(signal?: AbortSignal): Promise<UserSessionPage> {
    return this.#request("api/v1/users/me/sessions", parseUserSessionPage, {
      signal,
    });
  }

  createUserSession(
    request: UserSessionCreateRequest,
    signal?: AbortSignal,
  ): Promise<UserSessionProvision> {
    return this.#request(
      "api/v1/users/me/sessions",
      parseUserSessionProvision,
      { method: "POST", body: request, signal },
    );
  }

  renameCurrentUserSession(
    request: UserSessionUpdateRequest,
    signal?: AbortSignal,
  ): Promise<UserSession> {
    return this.#request(
      "api/v1/users/me/sessions/current",
      parseUserSession,
      { method: "PATCH", body: request, signal },
    );
  }

  revokeCurrentUserSession(signal?: AbortSignal): Promise<void> {
    return this.#request(
      "api/v1/users/me/sessions/current",
      () => undefined,
      { method: "DELETE", signal },
    );
  }

  revokeUserSession(sessionId: string, signal?: AbortSignal): Promise<void> {
    return this.#request(
      `api/v1/users/me/sessions/${encodeURIComponent(sessionId)}`,
      () => undefined,
      { method: "DELETE", signal },
    );
  }

  listConversations(
    options: {
      cursor?: ConversationCursor;
      includeArchived?: boolean;
      signal?: AbortSignal;
    } = {},
  ): Promise<ConversationPage> {
    const query = new URLSearchParams({ limit: "50" });
    if (options.cursor !== undefined) {
      query.set("cursor_updated_at", options.cursor.updated_at);
      query.set("cursor_id", options.cursor.id);
    }
    if (options.includeArchived !== undefined) {
      query.set("include_archived", String(options.includeArchived));
    }
    return this.#request(`api/v1/conversations?${query.toString()}`, parseConversationPage, {
      signal: options.signal,
    });
  }

  searchConversations(
    request: ConversationSearchRequest,
    signal?: AbortSignal,
  ): Promise<ConversationPage> {
    return this.#request(
      "api/v1/conversations/search",
      parseConversationPage,
      { method: "POST", body: request, signal },
    );
  }

  getConversation(id: string, signal?: AbortSignal): Promise<ConversationSummary> {
    return this.#request(`api/v1/conversations/${encodeURIComponent(id)}`, parseConversation, { signal });
  }

  renameConversation(
    id: string,
    request: ConversationRenameRequest,
    signal?: AbortSignal,
  ): Promise<ConversationSummary> {
    return this.#request(
      `api/v1/conversations/${encodeURIComponent(id)}`,
      parseConversation,
      { method: "PATCH", body: request, signal },
    );
  }

  updateConversationState(
    id: string,
    request: ConversationStateUpdateRequest,
    signal?: AbortSignal,
  ): Promise<ConversationSummary> {
    return this.#request(
      `api/v1/conversations/${encodeURIComponent(id)}/state`,
      parseConversation,
      { method: "PATCH", body: request, signal },
    );
  }

  forkConversation(
    id: string,
    request: ConversationForkRequest = {},
    signal?: AbortSignal,
  ): Promise<ConversationSummary> {
    return this.#request(
      `api/v1/conversations/${encodeURIComponent(id)}/fork`,
      parseConversation,
      { method: "POST", body: request, signal },
    );
  }

  async deleteConversation(id: string, signal?: AbortSignal): Promise<void> {
    await this.#request(
      `api/v1/conversations/${encodeURIComponent(id)}`,
      () => undefined,
      { method: "DELETE", signal },
    );
  }

  createConversation(
    request: ConversationCreateRequest,
    signal?: AbortSignal,
  ): Promise<ConversationCreateResponse> {
    return this.#request("api/v1/conversations", parseConversationCreateResponse, {
      method: "POST",
      body: request,
      signal,
    });
  }

  listMessages(id: string, signal?: AbortSignal): Promise<MessagePage> {
    return this.listMessagesPage(id, { signal });
  }

  listMessagesPage(
    id: string,
    options: { cursor?: number; limit?: number; signal?: AbortSignal } = {},
  ): Promise<MessagePage> {
    const query = new URLSearchParams({ limit: String(options.limit ?? 100) });
    if (options.cursor !== undefined) query.set("cursor", String(options.cursor));
    return this.#request(
      `api/v1/conversations/${encodeURIComponent(id)}/messages?${query.toString()}`,
      parseMessagePage,
      { signal: options.signal },
    );
  }

  generate(
    id: string,
    request: ConversationTextGenerationRequest,
    signal?: AbortSignal,
  ): Promise<ConversationTextGenerationResponse> {
    return this.#request(
      `api/v1/conversations/${encodeURIComponent(id)}/messages/generate`,
      parseGenerationResponse,
      { method: "POST", body: request, signal },
    );
  }

  async uploadAsset(file: MobileUpload, signal?: AbortSignal): Promise<Asset> {
    const form = new FormData();
    form.append("file", { uri: file.uri, name: file.name, type: file.mimeType } as unknown as Blob);
    let response: Response;
    try {
      response = await this.#fetch(new URL("api/v1/assets", `${this.#baseUrl}/`), {
        method: "POST",
        headers: {
          Accept: "application/json",
          Authorization: `Bearer ${this.#token}`,
          "Idempotency-Key": createIdempotencyKey(),
        },
        body: form,
        signal,
      });
    } catch (error) {
      if (signal?.aborted || (error instanceof Error && error.name === "AbortError")) {
        throw new MobileApiError("cancelled", "Upload cancelled.");
      }
      throw new MobileApiError("network", "Could not upload the private attachment.");
    }
    const requestId = response.headers.get("X-Request-ID");
    if (!response.ok) throw statusError(response.status, requestId);
    try {
      return parseAsset(await response.json());
    } catch {
      throw new MobileApiError("server", "The backend returned an invalid attachment.", response.status, requestId);
    }
  }

  transcribe(assetId: string, modelId: string, signal?: AbortSignal): Promise<VoiceTranscription> {
    return this.#request("api/v1/voice/transcriptions", parseVoiceTranscription, {
      method: "POST",
      body: { asset_id: assetId, model_id: modelId },
      signal,
    });
  }

  synthesizeVoice(
    request: VoiceSynthesisRequest,
    signal?: AbortSignal,
  ): Promise<VoiceSynthesis> {
    return this.#request("api/v1/voice/syntheses", parseVoiceSynthesis, {
      method: "POST",
      body: request,
      headers: { "Idempotency-Key": createIdempotencyKey() },
      signal,
    });
  }

  generateImage(
    request: ImageGenerationRequest,
    signal?: AbortSignal,
  ): Promise<ImageOperation> {
    return this.#request("api/v1/images/generations", parseImageOperation, {
      method: "POST",
      body: request,
      headers: { "Idempotency-Key": createIdempotencyKey() },
      signal,
    });
  }

  editImage(request: ImageEditingRequest, signal?: AbortSignal): Promise<ImageOperation> {
    return this.#request("api/v1/images/edits", parseImageOperation, {
      method: "POST",
      body: request,
      headers: { "Idempotency-Key": createIdempotencyKey() },
      signal,
    });
  }

  async downloadAsset(assetId: string, signal?: AbortSignal): Promise<PrivateAssetContent> {
    let response: Response;
    try {
      response = await this.#fetch(
        new URL(`api/v1/assets/${encodeURIComponent(assetId)}/content`, `${this.#baseUrl}/`),
        {
          headers: {
            Accept: "application/octet-stream",
            Authorization: `Bearer ${this.#token}`,
          },
          signal,
        },
      );
    } catch (error) {
      if (signal?.aborted || (error instanceof Error && error.name === "AbortError")) {
        throw new MobileApiError("cancelled", "Request cancelled.");
      }
      throw new MobileApiError("network", "Could not load the private media.");
    }
    const requestId = response.headers.get("X-Request-ID");
    if (!response.ok) throw statusError(response.status, requestId);
    const mediaType = response.headers.get("X-Asset-Media-Type");
    const declaredLength = Number(response.headers.get("Content-Length"));
    if (
      mediaType === null ||
      !mediaTypePattern.test(mediaType) ||
      (Number.isFinite(declaredLength) && declaredLength > MAX_MOBILE_MEDIA_BYTES)
    ) {
      throw new MobileApiError("validation", "The private media cannot be opened safely.", response.status, requestId);
    }
    try {
      const bytes = new Uint8Array(await response.arrayBuffer());
      if (bytes.byteLength === 0 || bytes.byteLength > MAX_MOBILE_MEDIA_BYTES) {
        throw new Error("invalid media size");
      }
      return { bytes, mediaType };
    } catch {
      throw new MobileApiError("server", "The backend returned invalid private media.", response.status, requestId);
    }
  }

  async deleteAsset(assetId: string, signal?: AbortSignal): Promise<void> {
    await this.#request(
      `api/v1/assets/${encodeURIComponent(assetId)}`,
      () => undefined,
      { method: "DELETE", signal },
    );
  }
}
