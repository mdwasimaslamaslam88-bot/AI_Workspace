import {
  type Asset,
  type ConversationCreateRequest,
  type ConversationCreateResponse,
  type ConversationPage,
  type ConversationSummary,
  type ConversationTextGenerationRequest,
  type ConversationTextGenerationResponse,
  type CurrentUser,
  type LocalModelPage,
  type MessagePage,
  type ProductCapabilityPage,
  type VoiceTranscription,
  parseAsset,
  parseConversation,
  parseConversationCreateResponse,
  parseConversationPage,
  parseCurrentUser,
  parseGenerationResponse,
  parseMessagePage,
  parseModelPage,
  parseProductCapabilityPage,
  parseVoiceTranscription,
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
    options: { method?: "GET" | "POST" | "DELETE"; body?: unknown; signal?: AbortSignal } = {},
  ): Promise<T> {
    let response: Response;
    try {
      response = await this.#fetch(new URL(path, `${this.#baseUrl}/`), {
        method: options.method ?? "GET",
        headers: {
          Accept: "application/json",
          Authorization: `Bearer ${this.#token}`,
          ...(options.body === undefined ? {} : { "Content-Type": "application/json" }),
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

  listModels(signal?: AbortSignal): Promise<LocalModelPage> {
    return this.#request("api/v1/ai/models", parseModelPage, { signal });
  }

  getCapabilities(signal?: AbortSignal): Promise<ProductCapabilityPage> {
    return this.#request("api/v1/ai/capabilities", parseProductCapabilityPage, { signal });
  }

  listConversations(signal?: AbortSignal): Promise<ConversationPage> {
    return this.#request("api/v1/conversations?limit=50", parseConversationPage, { signal });
  }

  getConversation(id: string, signal?: AbortSignal): Promise<ConversationSummary> {
    return this.#request(`api/v1/conversations/${encodeURIComponent(id)}`, parseConversation, { signal });
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
    return this.#request(
      `api/v1/conversations/${encodeURIComponent(id)}/messages?limit=100`,
      parseMessagePage,
      { signal },
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
          "Idempotency-Key": `mobile-${Date.now()}-${Math.random().toString(36).slice(2)}`,
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

  async deleteAsset(assetId: string, signal?: AbortSignal): Promise<void> {
    await this.#request(
      `api/v1/assets/${encodeURIComponent(assetId)}`,
      () => undefined,
      { method: "DELETE", signal },
    );
  }
}
