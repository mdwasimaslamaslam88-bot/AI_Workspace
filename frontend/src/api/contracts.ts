export type UUID = string;
export type Timestamp = string;

export type MessageRole = "system" | "user" | "assistant" | "tool";
export type ModelModality = "text";
export type ModelAvailability = "available" | "unavailable" | "unknown";
export type ModelCapability =
  | "text_generation"
  | "chat"
  | "code"
  | "streaming"
  | "tool_calling"
  | "structured_output"
  | "vision_input"
  | "embeddings";

export interface CurrentUser {
  id: UUID;
  created_at: Timestamp;
  updated_at: Timestamp;
}

export interface LocalModel {
  model_id: string;
  display_name: string;
  runtime_id: string;
  modality: ModelModality;
  family: string | null;
  parameter_class: string | null;
  capabilities: ModelCapability[];
  context_window: number | null;
  quantization: string | null;
  estimated_vram_bytes: number | null;
  availability: ModelAvailability;
}

export interface LocalModelPage {
  items: LocalModel[];
}

export interface ConversationSummary {
  id: UUID;
  title: string | null;
  created_at: Timestamp;
  updated_at: Timestamp;
}

export interface ConversationCursor {
  updated_at: Timestamp;
  id: UUID;
}

export interface ConversationPage {
  items: ConversationSummary[];
  next_cursor: ConversationCursor | null;
}

export interface Message {
  id: UUID;
  conversation_id: UUID;
  role: MessageRole;
  content: string;
  sequence_number: number;
  created_at: Timestamp;
  updated_at: Timestamp;
}

export interface MessagePage {
  items: Message[];
  next_cursor: number | null;
}

export interface ConversationCreateRequest {
  initial_message: string;
  title?: string;
  system_prompt?: string;
}

export interface ConversationCreateResponse extends ConversationSummary {
  initial_message: Message;
}

export interface ConversationTextGenerationRequest {
  model_id: string;
  user_message?: string;
  max_output_tokens?: number;
  temperature?: number;
  seed?: number;
  top_p?: number;
  top_k?: number;
  min_p?: number;
  repeat_penalty?: number;
  repeat_last_n?: number;
  typical_p?: number;
  presence_penalty?: number;
  frequency_penalty?: number;
  stop_sequences?: string[];
}

export interface ConversationTextGenerationResponse {
  model_id: string;
  message: Message;
}

export interface BackendErrorEnvelope {
  success: false;
  error: {
    code: string;
    message: string;
    details?: unknown;
  };
  path: string;
  timestamp: string;
}

type UnknownRecord = Record<string, unknown>;

function invalidResponse(): never {
  throw new Error("Backend returned an invalid response.");
}

function record(value: unknown): UnknownRecord {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return invalidResponse();
  }
  return value as UnknownRecord;
}

function stringField(value: unknown): string {
  if (typeof value !== "string") return invalidResponse();
  return value;
}

function nullableString(value: unknown): string | null {
  return value === null ? null : stringField(value);
}

function integerOrNull(value: unknown): number | null {
  if (value === null) return null;
  if (!Number.isSafeInteger(value)) return invalidResponse();
  return value as number;
}

function enumField<T extends string>(value: unknown, values: readonly T[]): T {
  if (typeof value !== "string" || !values.includes(value as T)) {
    return invalidResponse();
  }
  return value as T;
}

const messageRoles = ["system", "user", "assistant", "tool"] as const;
const modelCapabilities = [
  "text_generation",
  "chat",
  "code",
  "streaming",
  "tool_calling",
  "structured_output",
  "vision_input",
  "embeddings",
] as const;
const modelAvailabilities = ["available", "unavailable", "unknown"] as const;

export function parseCurrentUser(value: unknown): CurrentUser {
  const item = record(value);
  return {
    id: stringField(item.id),
    created_at: stringField(item.created_at),
    updated_at: stringField(item.updated_at),
  };
}

export function parseModel(value: unknown): LocalModel {
  const item = record(value);
  if (!Array.isArray(item.capabilities)) return invalidResponse();
  return {
    model_id: stringField(item.model_id),
    display_name: stringField(item.display_name),
    runtime_id: stringField(item.runtime_id),
    modality: enumField(item.modality, ["text"] as const),
    family: nullableString(item.family),
    parameter_class: nullableString(item.parameter_class),
    capabilities: item.capabilities.map((capability) =>
      enumField(capability, modelCapabilities),
    ),
    context_window: integerOrNull(item.context_window),
    quantization: nullableString(item.quantization),
    estimated_vram_bytes: integerOrNull(item.estimated_vram_bytes),
    availability: enumField(item.availability, modelAvailabilities),
  };
}

export function parseModelPage(value: unknown): LocalModelPage {
  const page = record(value);
  if (!Array.isArray(page.items)) return invalidResponse();
  return { items: page.items.map(parseModel) };
}

export function parseConversation(value: unknown): ConversationSummary {
  const item = record(value);
  return {
    id: stringField(item.id),
    title: nullableString(item.title),
    created_at: stringField(item.created_at),
    updated_at: stringField(item.updated_at),
  };
}

export function parseMessage(value: unknown): Message {
  const item = record(value);
  const sequenceNumber = integerOrNull(item.sequence_number);
  if (sequenceNumber === null) return invalidResponse();
  return {
    id: stringField(item.id),
    conversation_id: stringField(item.conversation_id),
    role: enumField(item.role, messageRoles),
    content: stringField(item.content),
    sequence_number: sequenceNumber,
    created_at: stringField(item.created_at),
    updated_at: stringField(item.updated_at),
  };
}

export function parseConversationPage(value: unknown): ConversationPage {
  const page = record(value);
  if (!Array.isArray(page.items)) return invalidResponse();
  let nextCursor: ConversationCursor | null = null;
  if (page.next_cursor !== null) {
    const cursor = record(page.next_cursor);
    nextCursor = {
      updated_at: stringField(cursor.updated_at),
      id: stringField(cursor.id),
    };
  }
  return {
    items: page.items.map(parseConversation),
    next_cursor: nextCursor,
  };
}

export function parseConversationCreateResponse(
  value: unknown,
): ConversationCreateResponse {
  const item = record(value);
  return {
    ...parseConversation(item),
    initial_message: parseMessage(item.initial_message),
  };
}

export function parseMessagePage(value: unknown): MessagePage {
  const page = record(value);
  if (!Array.isArray(page.items)) return invalidResponse();
  return {
    items: page.items.map(parseMessage),
    next_cursor: integerOrNull(page.next_cursor),
  };
}

export function parseGenerationResponse(
  value: unknown,
): ConversationTextGenerationResponse {
  const response = record(value);
  return {
    model_id: stringField(response.model_id),
    message: parseMessage(response.message),
  };
}
