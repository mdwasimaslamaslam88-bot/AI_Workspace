export type UUID = string;
export type Timestamp = string;

export type MessageRole = "system" | "user" | "assistant" | "tool";
export type AttachmentState = "active" | "deleted";

export interface Asset {
  id: UUID;
  original_filename: string | null;
  media_type: string;
  byte_size: number;
  content_sha256: string;
  created_at: Timestamp;
  deleted_at: Timestamp | null;
}

export interface MessageAttachment {
  id: UUID;
  position: number;
  state: AttachmentState;
  original_filename: string | null;
  media_type: string | null;
  byte_size: number | null;
}


export interface MessageCitation {
  asset_id: UUID;
  position: number;
  state: AttachmentState;
  original_filename: string | null;
  page_number: number | null;
  row_start: number | null;
  row_end: number | null;
  section: string | null;
  excerpt: string | null;
}

export type DocumentStatus =
  | "pending"
  | "processing"
  | "ready"
  | "failed"
  | "cancelled";

export interface IndexedDocument {
  id: UUID;
  asset_id: UUID;
  status: DocumentStatus;
  source_state: AttachmentState;
  original_filename: string | null;
  media_type: string | null;
  chunk_count: number;
  character_count: number;
  failure_code: string | null;
  created_at: Timestamp;
  updated_at: Timestamp;
  completed_at: Timestamp | null;
}

export type MemoryCategory =
  | "preference"
  | "fact"
  | "instruction"
  | "project_context";

export interface PersonalMemory {
  id: UUID;
  category: MemoryCategory;
  state: "active" | "deleted";
  content: string | null;
  provenance_kind: "explicit_user_entry";
  created_at: Timestamp;
  updated_at: Timestamp;
  deleted_at: Timestamp | null;
}

export interface MemoryPage {
  items: PersonalMemory[];
}

export interface MemorySetting {
  enabled: boolean;
  created_at: Timestamp | null;
  updated_at: Timestamp | null;
}

export interface MemoryCreateRequest {
  category: MemoryCategory;
  content: string;
}

export type JsonValue =
  | null
  | boolean
  | number
  | string
  | JsonValue[]
  | { [key: string]: JsonValue };

export type ToolExecutionStatus =
  | "running"
  | "completed"
  | "failed"
  | "timed_out"
  | "cancelled";

export interface ToolDescriptor {
  name: string;
  description: string;
  input_schema: { [key: string]: JsonValue };
  permission: string;
  timeout_seconds: number;
  max_output_characters: number;
}

export interface ToolDescriptorPage {
  items: ToolDescriptor[];
}

export interface ToolExecution {
  id: UUID;
  conversation_id: UUID | null;
  tool_name: string;
  permission: string;
  status: ToolExecutionStatus;
  initiator: "explicit_user";
  arguments: { [key: string]: JsonValue };
  result: JsonValue;
  error_code: string | null;
  started_at: Timestamp;
  completed_at: Timestamp | null;
  duration_ms: number | null;
}

export interface ToolExecutionPage {
  items: ToolExecution[];
}

export interface ToolExecutionRequest {
  arguments: { [key: string]: JsonValue };
  conversation_id?: UUID;
}

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
  attachments: MessageAttachment[];
  citations: MessageCitation[];
}

export interface MessagePage {
  items: Message[];
  next_cursor: number | null;
}

export interface ConversationCreateRequest {
  initial_message: string;
  title?: string;
  system_prompt?: string;
  attachment_ids?: UUID[];
}

export interface ConversationCreateResponse extends ConversationSummary {
  initial_message: Message;
}

export interface ConversationTextGenerationRequest {
  model_id: string;
  user_message?: string;
  attachment_ids?: UUID[];
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
export function parseAsset(value: unknown): Asset {
  const item = record(value);
  const byteSize = integerOrNull(item.byte_size);
  const contentSha256 = stringField(item.content_sha256);
  if (
    byteSize === null || byteSize < 1 || !/^[0-9a-f]{64}$/.test(contentSha256)
  ) {
    return invalidResponse();
  }
  return {
    id: stringField(item.id),
    original_filename: nullableString(item.original_filename),
    media_type: stringField(item.media_type),
    byte_size: byteSize,
    content_sha256: contentSha256,
    created_at: stringField(item.created_at),
    deleted_at: nullableString(item.deleted_at),
  };
}

export function parseIndexedDocument(value: unknown): IndexedDocument {
  const item = record(value);
  const chunkCount = integerOrNull(item.chunk_count);
  const characterCount = integerOrNull(item.character_count);
  if (
    chunkCount === null ||
    chunkCount < 0 ||
    characterCount === null ||
    characterCount < 0
  ) {
    return invalidResponse();
  }
  return {
    id: stringField(item.id),
    asset_id: stringField(item.asset_id),
    status: enumField(item.status, [
      "pending",
      "processing",
      "ready",
      "failed",
      "cancelled",
    ] as const),
    source_state: enumField(item.source_state, ["active", "deleted"] as const),
    original_filename: nullableString(item.original_filename),
    media_type: nullableString(item.media_type),
    chunk_count: chunkCount,
    character_count: characterCount,
    failure_code: nullableString(item.failure_code),
    created_at: stringField(item.created_at),
    updated_at: stringField(item.updated_at),
    completed_at: nullableString(item.completed_at),
  };
}

export function parsePersonalMemory(value: unknown): PersonalMemory {
  const item = record(value);
  const state = enumField(item.state, ["active", "deleted"] as const);
  const content = nullableString(item.content);
  const deletedAt = nullableString(item.deleted_at);
  if (
    (state === "active" &&
      (content === null || !content.trim() || content.length > 2_000 || deletedAt !== null)) ||
    (state === "deleted" && (content !== null || deletedAt === null))
  ) {
    return invalidResponse();
  }
  return {
    id: stringField(item.id),
    category: enumField(item.category, [
      "preference",
      "fact",
      "instruction",
      "project_context",
    ] as const),
    state,
    content,
    provenance_kind: enumField(item.provenance_kind, [
      "explicit_user_entry",
    ] as const),
    created_at: stringField(item.created_at),
    updated_at: stringField(item.updated_at),
    deleted_at: deletedAt,
  };
}

export function parseMemoryPage(value: unknown): MemoryPage {
  const page = record(value);
  if (!Array.isArray(page.items)) return invalidResponse();
  return { items: page.items.map(parsePersonalMemory) };
}

export function parseMemorySetting(value: unknown): MemorySetting {
  const item = record(value);
  if (typeof item.enabled !== "boolean") return invalidResponse();
  return {
    enabled: item.enabled,
    created_at: nullableString(item.created_at),
    updated_at: nullableString(item.updated_at),
  };
}

export function parseMessageAttachment(value: unknown): MessageAttachment {
  const item = record(value);
  const position = integerOrNull(item.position);
  const state = enumField(item.state, ["active", "deleted"] as const);
  const originalFilename = nullableString(item.original_filename);
  const mediaType = nullableString(item.media_type);
  const byteSize = integerOrNull(item.byte_size);
  if (position === null || position < 1) return invalidResponse();
  if (
    (state === "deleted" &&
      (originalFilename !== null || mediaType !== null || byteSize !== null)) ||
    (state === "active" &&
      (mediaType === null || byteSize === null || byteSize < 1))
  ) {
    return invalidResponse();
  }
  return {
    id: stringField(item.id),
    position,
    state,
    original_filename: originalFilename,
    media_type: mediaType,
    byte_size: byteSize,
  };
}

export function parseMessageCitation(value: unknown): MessageCitation {
  const item = record(value);
  const position = integerOrNull(item.position);
  const pageNumber = integerOrNull(item.page_number);
  const rowStart = integerOrNull(item.row_start);
  const rowEnd = integerOrNull(item.row_end);
  const state = enumField(item.state, ["active", "deleted"] as const);
  const originalFilename = nullableString(item.original_filename);
  const excerpt = nullableString(item.excerpt);
  if (
    position === null ||
    position < 1 ||
    (pageNumber !== null && pageNumber < 1) ||
    (rowStart !== null && rowStart < 1) ||
    (rowEnd !== null && (rowStart === null || rowEnd < rowStart)) ||
    (state === "deleted" &&
      (originalFilename !== null || excerpt !== null))
  ) {
    return invalidResponse();
  }
  return {
    asset_id: stringField(item.asset_id),
    position,
    state,
    original_filename: originalFilename,
    page_number: pageNumber,
    row_start: rowStart,
    row_end: rowEnd,
    section: nullableString(item.section),
    excerpt,
  };
}

export function parseMessage(value: unknown): Message {
  const item = record(value);
  const sequenceNumber = integerOrNull(item.sequence_number);
  const citations = item.citations ?? [];
  if (sequenceNumber === null || !Array.isArray(item.attachments) || !Array.isArray(citations)) {
    return invalidResponse();
  }
  return {
    id: stringField(item.id),
    conversation_id: stringField(item.conversation_id),
    role: enumField(item.role, messageRoles),
    content: stringField(item.content),
    sequence_number: sequenceNumber,
    created_at: stringField(item.created_at),
    updated_at: stringField(item.updated_at),
    attachments: item.attachments.map(parseMessageAttachment),
    citations: citations.map(parseMessageCitation),
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

function jsonValue(value: unknown, depth = 0): JsonValue {
  if (depth > 12) return invalidResponse();
  if (
    value === null ||
    typeof value === "boolean" ||
    typeof value === "string"
  ) {
    if (typeof value === "string" && value.length > 16_384) {
      return invalidResponse();
    }
    return value;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return invalidResponse();
    return value;
  }
  if (Array.isArray(value)) {
    if (value.length > 100) return invalidResponse();
    return value.map((item) => jsonValue(item, depth + 1));
  }
  const item = record(value);
  if (Object.keys(item).length > 100) return invalidResponse();
  return Object.fromEntries(
    Object.entries(item).map(([key, child]) => [key, jsonValue(child, depth + 1)]),
  );
}

export function parseToolDescriptor(value: unknown): ToolDescriptor {
  const item = record(value);
  const timeout = item.timeout_seconds;
  const maximum = integerOrNull(item.max_output_characters);
  const inputSchema = jsonValue(item.input_schema);
  if (
    typeof timeout !== "number" ||
    !Number.isFinite(timeout) ||
    timeout <= 0 ||
    timeout > 5 ||
    maximum === null ||
    maximum < 1 ||
    maximum > 16_384 ||
    inputSchema === null ||
    Array.isArray(inputSchema) ||
    typeof inputSchema !== "object"
  ) {
    return invalidResponse();
  }
  return {
    name: stringField(item.name),
    description: stringField(item.description),
    input_schema: inputSchema,
    permission: stringField(item.permission),
    timeout_seconds: timeout,
    max_output_characters: maximum,
  };
}

export function parseToolDescriptorPage(value: unknown): ToolDescriptorPage {
  const page = record(value);
  if (!Array.isArray(page.items)) return invalidResponse();
  return { items: page.items.map(parseToolDescriptor) };
}

export function parseToolExecution(value: unknown): ToolExecution {
  const item = record(value);
  const status = enumField(item.status, [
    "running",
    "completed",
    "failed",
    "timed_out",
    "cancelled",
  ] as const);
  const argumentsValue = jsonValue(item.arguments);
  const result = jsonValue(item.result);
  const completedAt = nullableString(item.completed_at);
  const duration = integerOrNull(item.duration_ms);
  const errorCode = nullableString(item.error_code);
  if (
    argumentsValue === null ||
    Array.isArray(argumentsValue) ||
    typeof argumentsValue !== "object" ||
    duration !== null && duration < 0 ||
    (status === "running" &&
      (completedAt !== null || duration !== null || result !== null || errorCode !== null)) ||
    (status === "completed" &&
      (completedAt === null || duration === null || result === null || errorCode !== null)) ||
    (["failed", "timed_out", "cancelled"] as const).includes(
      status as "failed" | "timed_out" | "cancelled",
    ) && (completedAt === null || duration === null || result !== null || errorCode === null)
  ) {
    return invalidResponse();
  }
  return {
    id: stringField(item.id),
    conversation_id: nullableString(item.conversation_id),
    tool_name: stringField(item.tool_name),
    permission: stringField(item.permission),
    status,
    initiator: enumField(item.initiator, ["explicit_user"] as const),
    arguments: argumentsValue,
    result,
    error_code: errorCode,
    started_at: stringField(item.started_at),
    completed_at: completedAt,
    duration_ms: duration,
  };
}

export function parseToolExecutionPage(value: unknown): ToolExecutionPage {
  const page = record(value);
  if (!Array.isArray(page.items)) return invalidResponse();
  return { items: page.items.map(parseToolExecution) };
}
