export type UUID = string;
export type Timestamp = string;

export type MessageRole = "system" | "user" | "assistant" | "tool";
export type AttachmentState = "active" | "deleted";
export type AssetProvenanceKind =
  | "upload"
  | "image_generation"
  | "image_editing"
  | "speech_synthesis";

export interface Asset {
  id: UUID;
  original_filename: string | null;
  media_type: string;
  byte_size: number;
  content_sha256: string;
  provenance_kind: AssetProvenanceKind;
  source_asset_id: UUID | null;
  runtime_id: string | null;
  model_id: string | null;
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
  provenance_kind: AssetProvenanceKind | null;
  source_asset_id: UUID | null;
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

export interface VoiceTranscriptionRequest {
  asset_id: UUID;
  model_id: string;
}

export interface VoiceTranscription {
  text: string;
  language: string | null;
  duration_seconds: number;
}

export interface VoiceSynthesisRequest {
  model_id: string;
  text: string;
}

export interface VoiceSynthesis {
  asset: Asset;
  created: boolean;
}

export interface ImageGenerationRequest {
  conversation_id: UUID;
  model_id: string;
  prompt: string;
  negative_prompt?: string;
  width?: number;
  height?: number;
  steps?: number;
  guidance?: number;
  seed?: number;
}

export interface ImageEditingRequest {
  conversation_id: UUID;
  model_id: string;
  source_asset_id: UUID;
  mask_asset_id?: UUID;
  instruction: string;
  negative_prompt?: string;
  steps?: number;
  guidance?: number;
  denoise?: number;
  seed?: number;
}

export interface ImageOperation {
  asset: Asset;
  message: Message;
  created: boolean;
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
  initiator: "explicit_user" | "workflow";
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

export type WorkflowStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "timed_out";

export interface WorkflowStep {
  id: UUID;
  position: number;
  tool_name: string;
  permission: string;
  arguments: { [key: string]: JsonValue };
  status: WorkflowStatus;
  tool_execution_id: UUID | null;
  result: JsonValue;
  error_code: string | null;
  started_at: Timestamp | null;
  completed_at: Timestamp | null;
  duration_ms: number | null;
}

export interface Workflow {
  id: UUID;
  name: string | null;
  status: WorkflowStatus;
  step_count: number;
  current_step_position: number | null;
  cancel_requested: boolean;
  result: JsonValue;
  error_code: string | null;
  created_at: Timestamp;
  updated_at: Timestamp;
  started_at: Timestamp | null;
  completed_at: Timestamp | null;
  steps: WorkflowStep[];
}

export interface WorkflowPage {
  items: Workflow[];
}

export interface WorkflowStepCreateRequest {
  tool_name: string;
  arguments: { [key: string]: JsonValue };
}

export interface WorkflowCreateRequest {
  name?: string;
  steps: WorkflowStepCreateRequest[];
}

export type ModelModality = "text" | "image" | "audio" | "multimodal";
export type ModelAvailability = "available" | "unavailable" | "unknown";
export type ModelScaleClass =
  | "7b_8b"
  | "14b"
  | "30b_34b"
  | "70b"
  | "100b_plus"
  | "200b_plus"
  | "500b_plus"
  | "1000b_plus"
  | "2000b"
  | "moe_very_large";
export type HardwareClass =
  | "cpu_only"
  | "gpu_under_8gb"
  | "gpu_8_to_15gb"
  | "gpu_16_to_23gb"
  | "gpu_24_to_47gb"
  | "gpu_48_to_79gb"
  | "gpu_80gb_plus"
  | "multi_gpu";
export type ModelCapability =
  | "text_generation"
  | "chat"
  | "code"
  | "streaming"
  | "tool_calling"
  | "structured_output"
  | "vision_input"
  | "embeddings"
  | "image_generation"
  | "image_editing"
  | "speech_recognition"
  | "speech_synthesis";

export interface CurrentUser {
  id: UUID;
  created_at: Timestamp;
  updated_at: Timestamp;
}

export interface AccessTokenRotation {
  access_token: string;
  token_type: "bearer";
}

export interface UserSession {
  id: UUID;
  label: string | null;
  created_at: Timestamp;
  updated_at: Timestamp;
  is_current: boolean;
}

export interface UserSessionPage {
  items: UserSession[];
}

export interface UserSessionCreateRequest {
  label?: string | null;
}

export interface UserSessionUpdateRequest {
  label: string | null;
}

export interface UserSessionProvision extends AccessTokenRotation {
  session: UserSession;
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
  scale_class: ModelScaleClass | null;
  required_vram_bytes: number | null;
  required_ram_bytes: number | null;
  installed: boolean;
  runnable_now: boolean;
  future_capable: boolean;
  hardware_class: HardwareClass | null;
  fallback_model_id: string | null;
}

export interface LocalModelPage {
  items: LocalModel[];
}

export type ProductCapabilityId =
  | "chat"
  | "vision_input"
  | "attachments"
  | "documents_rag"
  | "personal_memory"
  | "bounded_tools"
  | "bounded_workflows"
  | "image_generation"
  | "image_editing"
  | "voice_input"
  | "voice_output";

export type ProductCapabilityReason =
  | "asset_storage_required"
  | "local_model_runtime_unavailable"
  | "allowlisted_text_model_required"
  | "allowlisted_vision_model_required"
  | "local_image_runtime_and_model_required"
  | "local_image_edit_runtime_and_model_required"
  | "local_voice_runtime_and_models_required";

export interface ProductCapability {
  id: ProductCapabilityId;
  status: "available" | "unavailable";
  blocking_reasons: ProductCapabilityReason[];
}

export interface ProductCapabilityPage {
  items: ProductCapability[];
}

export type FeatureLayer =
  | "ai_presence"
  | "mission_control"
  | "universal_workspace"
  | "ai_command_center"
  | "apps_hub";

export type FeatureStatus =
  | "implemented"
  | "runtime_dependent"
  | "external_dependency"
  | "planned";

export interface ProductFeature {
  id: string;
  layer: FeatureLayer;
  category: string;
  title: string;
  description: string;
  ui_entry_point: string;
  backend_capability: string;
  required_permissions: string[];
  dependencies: string[];
  status: FeatureStatus;
  test_coverage: string[];
}

export interface FeatureRegistry {
  schema_version: 1;
  product: "AI OS";
  count: number;
  items: ProductFeature[];
}

export type DiagnosticStatus = "ready" | "unavailable" | "unconfigured";
export type DiagnosticServiceId =
  | "backend"
  | "database"
  | "redis"
  | "ollama"
  | "vision"
  | "image_runtime"
  | "speech_to_text"
  | "text_to_speech"
  | "storage"
  | "remote_gateway"
  | "gpu";

export interface ServiceDiagnostic {
  id: DiagnosticServiceId;
  status: DiagnosticStatus;
}

export interface GpuDiagnostic {
  model: string;
  vram_bytes: number;
  free_vram_bytes?: number | null;
  vendor?: string | null;
  compute_capability?: string | null;
  driver_version?: string | null;
  runtime?: string | null;
  runtime_version?: string | null;
  hardware_class: HardwareClass;
  status: "ready";
}

export interface HardwareDiagnostic {
  fingerprint: string;
  profile_gib: number;
  gpu_count: number;
  total_ram_bytes: number;
  available_ram_bytes: number | null;
  swap_total_bytes: number;
  swap_free_bytes: number;
  storage_total_bytes: number | null;
  storage_free_bytes: number | null;
  cpu_model: string;
  cpu_logical_count: number;
  os_name: string;
  os_version: string;
  architecture: string;
  upgrade_detected: boolean;
  capability_cache_invalidated: boolean;
  restart_required: boolean;
  runtime_validated: boolean;
}

export interface ModelEligibilityDiagnostic {
  model_id: string;
  display_name: string;
  runtime_id: string;
  status: typeof modelEligibilityStatuses[number];
  reasons: Array<typeof modelAdmissionReasons[number]>;
  performance: typeof performanceClasses[number];
  verified: boolean;
  fallback_model_id: string | null;
}

export interface ModelRouteDiagnostic {
  task: ModelTask;
  model_id: string;
  fallback_model_ids: string[];
  inference_mode: "auto" | "thinking_disabled";
}

export interface SystemDiagnostics {
  mode: "local" | "remote";
  services: ServiceDiagnostic[];
  gpus: GpuDiagnostic[];
  hardware?: HardwareDiagnostic | null;
  models?: ModelEligibilityDiagnostic[];
  routes?: ModelRouteDiagnostic[];
  external_providers?: ExternalProviderDiagnostic[];
  agents?: AgentRuntimeDiagnostic | null;
  self_update?: {
    configured: boolean;
    status: SelfUpdateState;
    checkpoint_ready: boolean;
    rollback_ready: boolean;
  };
  security_events?: SecurityEventDiagnostic[];
}

export interface ExternalProviderDiagnostic {
  provider_id: string;
  status: ExternalProviderStatus;
  spent_micros: number;
  spending_limit_micros: number;
  quota_remaining_tokens: number | null;
  verified_model_count: number;
}

export type AgentRuntimeStatus =
  | "queued"
  | "planning"
  | "running"
  | "verifying"
  | "retrying"
  | "completed"
  | "failed"
  | "cancelled"
  | "timed_out";

export interface AgentRuntimeDiagnostic {
  active_count: number;
  retained_count: number;
  statuses: Partial<Record<AgentRuntimeStatus, number>>;
}

export interface SecurityEventDiagnostic {
  kind:
    | "authentication_failure"
    | "rate_limit_containment"
    | "oversized_request_containment"
    | "application_error_containment";
  occurred_at: string;
}

export type ExternalProviderKind = "openai" | "anthropic" | "google";
export type ExternalProviderStatus =
  | "disabled"
  | "unconfigured"
  | "ready"
  | "rate_limited"
  | "quota_exhausted"
  | "spending_limit_reached"
  | "unavailable";

export interface ExternalModelPolicy {
  model_id: string;
  tasks: ModelTask[];
  verified: boolean;
  verification_evidence_sha256: string | null;
  measured_quality: number;
  measured_latency_ms: number;
  stability_rate: number;
  context_window: number;
  input_cost_micros_per_million_tokens: number;
  output_cost_micros_per_million_tokens: number;
}

export interface ExternalProvider {
  provider_id: string;
  kind: ExternalProviderKind;
  enabled: boolean;
  key_configured: boolean;
  free_tier: boolean;
  priority: number;
  timeout_seconds: number;
  rate_limit_requests_per_minute: number;
  spending_limit_micros: number;
  spent_micros: number;
  quota_remaining_tokens: number | null;
  status: ExternalProviderStatus;
  models: ExternalModelPolicy[];
}

export interface ExternalAISettings {
  configured: boolean;
  global_enabled: boolean;
  providers: ExternalProvider[];
  supported_provider_kinds: ExternalProviderKind[];
}

export interface ExternalModelPolicyRequest {
  model_id: string;
  tasks: ModelTask[];
  verified?: boolean;
  verification_evidence_sha256?: string;
  measured_quality?: number;
  measured_latency_ms?: number;
  stability_rate?: number;
  context_window?: number;
  input_cost_micros_per_million_tokens?: number;
  output_cost_micros_per_million_tokens?: number;
}

export interface ExternalProviderUpsertRequest {
  kind: ExternalProviderKind;
  api_key?: string;
  enabled?: boolean;
  free_tier?: boolean;
  priority?: number;
  timeout_seconds?: number;
  rate_limit_requests_per_minute?: number;
  spending_limit_micros?: number;
  quota_remaining_tokens?: number | null;
  models?: ExternalModelPolicyRequest[];
}

export type SelfUpdateState =
  | "idle"
  | "validating"
  | "ready"
  | "failed"
  | "activated"
  | "rolled_back"
  | "cancelled";

export interface SelfUpdateStatus {
  configured: boolean;
  status: SelfUpdateState;
  version: string | null;
  candidate_commit: string | null;
  checkpoint_ready: boolean;
  rollback_ready: boolean;
  activation_requires_owner: boolean;
  gates: Array<{ name: string; passed: boolean }>;
  failure_code: string | null;
}

export type AgentKind =
  | "planner"
  | "coding"
  | "debugging"
  | "research"
  | "browser"
  | "data"
  | "vision"
  | "image"
  | "voice"
  | "rag"
  | "automation"
  | "verifier";

export type AgentPermission =
  | "model_inference"
  | "workspace_read"
  | "workspace_write"
  | "build_execution"
  | "test_execution"
  | "network_research"
  | "browser_control"
  | "data_analysis"
  | "rag_read"
  | "memory_read"
  | "image_generation"
  | "image_editing"
  | "voice_input"
  | "voice_output"
  | "bounded_tool_execution";

export interface AgentOSCapabilities {
  profiles: Array<{
    kind: AgentKind;
    permissions: AgentPermission[];
    registered: boolean;
  }>;
  max_retries: number;
  max_deadline_seconds: number;
  active_runs: number;
  max_concurrency: number;
  persistence: string;
}

export interface AgentVerificationCheck {
  check_id: string;
  passed: boolean;
  failure: string;
  evidence_sha256: string | null;
}

export interface AgentAttempt {
  step_id: string;
  attempt: number;
  agent: AgentKind;
  model_id: string | null;
  verified: boolean;
  output_sha256: string;
  checks: AgentVerificationCheck[];
}

export interface AgentRun {
  id: string;
  task: ModelTask;
  specialist: AgentKind | null;
  status: AgentRuntimeStatus;
  created_at: string;
  updated_at: string;
  output: string | null;
  failure_code: string | null;
  attempts: AgentAttempt[];
}

export interface AgentRunPage { items: AgentRun[]; }

export interface AgentRunCreateRequest {
  goal: string;
  task: ModelTask;
  specialist?: AgentKind;
  max_retries?: number;
  deadline_seconds?: number;
  required_context_tokens?: number;
  require_objective_evidence?: boolean;
}

export interface ConversationSummary {
  id: UUID;
  title: string | null;
  is_pinned: boolean;
  is_archived: boolean;
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

export interface ConversationSearchRequest {
  query: string;
  limit?: number;
  cursor_updated_at?: Timestamp;
  cursor_id?: UUID;
  include_archived?: boolean;
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

export interface ConversationRenameRequest {
  title: string | null;
}

export type ConversationStateUpdateRequest =
  | { is_pinned: boolean; is_archived?: boolean }
  | { is_pinned?: boolean; is_archived: boolean };

export interface ConversationForkRequest {
  through_sequence_number?: number;
  replacement_content?: string;
}

export interface ConversationCreateResponse extends ConversationSummary {
  initial_message: Message;
}

export interface ConversationTextGenerationRequest {
  model_id?: string;
  task?: ModelTask;
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

export type ModelTask =
  | "general_chat"
  | "reasoning"
  | "mathematics"
  | "coding"
  | "debugging"
  | "code_generation"
  | "expert_analysis"
  | "vision"
  | "rag"
  | "memory"
  | "summarization"
  | "tool_calling"
  | "workflow_planning"
  | "long_context"
  | "exact_output"
  | "embedding"
  | "image_generation"
  | "image_editing"
  | "voice_input"
  | "voice_output";

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

function booleanField(value: unknown): boolean {
  if (typeof value !== "boolean") return invalidResponse();
  return value;
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
  "image_generation",
  "image_editing",
  "speech_recognition",
  "speech_synthesis",
] as const;
const modelAvailabilities = ["available", "unavailable", "unknown"] as const;
const modelScaleClasses = [
  "7b_8b",
  "14b",
  "30b_34b",
  "70b",
  "100b_plus",
  "200b_plus",
  "500b_plus",
  "1000b_plus",
  "2000b",
  "moe_very_large",
] as const;
const hardwareClasses = [
  "cpu_only",
  "gpu_under_8gb",
  "gpu_8_to_15gb",
  "gpu_16_to_23gb",
  "gpu_24_to_47gb",
  "gpu_48_to_79gb",
  "gpu_80gb_plus",
  "multi_gpu",
] as const;
const productCapabilityIds = [
  "chat",
  "vision_input",
  "attachments",
  "documents_rag",
  "personal_memory",
  "bounded_tools",
  "bounded_workflows",
  "image_generation",
  "image_editing",
  "voice_input",
  "voice_output",
] as const;
const productCapabilityReasons = [
  "asset_storage_required",
  "local_model_runtime_unavailable",
  "allowlisted_text_model_required",
  "allowlisted_vision_model_required",
  "local_image_runtime_and_model_required",
  "local_image_edit_runtime_and_model_required",
  "local_voice_runtime_and_models_required",
] as const;
const featureLayers = [
  "ai_presence",
  "mission_control",
  "universal_workspace",
  "ai_command_center",
  "apps_hub",
] as const;
const featureStatuses = [
  "implemented",
  "runtime_dependent",
  "external_dependency",
  "planned",
] as const;
const diagnosticStatuses = ["ready", "unavailable", "unconfigured"] as const;
const diagnosticServiceIds = [
  "backend",
  "database",
  "redis",
  "ollama",
  "vision",
  "image_runtime",
  "speech_to_text",
  "text_to_speech",
  "storage",
  "remote_gateway",
  "gpu",
] as const;
const modelTasks = [
  "general_chat",
  "reasoning",
  "mathematics",
  "coding",
  "debugging",
  "code_generation",
  "expert_analysis",
  "vision",
  "rag",
  "memory",
  "summarization",
  "tool_calling",
  "workflow_planning",
  "long_context",
  "exact_output",
  "embedding",
  "image_generation",
  "image_editing",
  "voice_input",
  "voice_output",
] as const;
const externalProviderKinds = ["openai", "anthropic", "google"] as const;
const externalProviderStatuses = [
  "disabled",
  "unconfigured",
  "ready",
  "rate_limited",
  "quota_exhausted",
  "spending_limit_reached",
  "unavailable",
] as const;
const selfUpdateStates = [
  "idle",
  "validating",
  "ready",
  "failed",
  "activated",
  "rolled_back",
  "cancelled",
] as const;
const agentRuntimeStatuses = [
  "queued",
  "planning",
  "running",
  "verifying",
  "retrying",
  "completed",
  "failed",
  "cancelled",
  "timed_out",
] as const;
const securityEventKinds = [
  "authentication_failure",
  "rate_limit_containment",
  "oversized_request_containment",
  "application_error_containment",
] as const;
const agentKinds = [
  "planner",
  "coding",
  "debugging",
  "research",
  "browser",
  "data",
  "vision",
  "image",
  "voice",
  "rag",
  "automation",
  "verifier",
] as const;
const agentPermissions = [
  "model_inference",
  "workspace_read",
  "workspace_write",
  "build_execution",
  "test_execution",
  "network_research",
  "browser_control",
  "data_analysis",
  "rag_read",
  "memory_read",
  "image_generation",
  "image_editing",
  "voice_input",
  "voice_output",
  "bounded_tool_execution",
] as const;
const modelEligibilityStatuses = [
  "runnable_now",
  "runnable_with_offload",
  "future_capable",
  "hardware_insufficient",
  "runtime_incompatible",
  "not_installed",
  "download_required",
  "verification_required",
  "disabled",
] as const;
const modelAdmissionReasons = [
  "eligible",
  "vram_insufficient",
  "ram_insufficient",
  "runtime_unsupported",
  "compute_capability_unsupported",
  "model_not_installed",
  "download_required",
  "multi_gpu_required",
  "verification_required",
  "model_disabled",
  "metadata_incomplete",
  "offload_too_slow",
  "model_unavailable",
] as const;
const performanceClasses = [
  "interactive",
  "acceptable",
  "slow",
  "experimental",
  "unsupported",
] as const;

export function parseCurrentUser(value: unknown): CurrentUser {
  const item = record(value);
  return {
    id: stringField(item.id),
    created_at: stringField(item.created_at),
    updated_at: stringField(item.updated_at),
  };
}

export function parseAccessTokenRotation(value: unknown): AccessTokenRotation {
  const item = record(value);
  const accessToken = stringField(item.access_token);
  if (!/^[A-Za-z0-9_-]{43}$/.test(accessToken)) return invalidResponse();
  return {
    access_token: accessToken,
    token_type: enumField(item.token_type, ["bearer"] as const),
  };
}

export function parseUserSession(value: unknown): UserSession {
  const item = record(value);
  return {
    id: stringField(item.id),
    label: nullableString(item.label),
    created_at: stringField(item.created_at),
    updated_at: stringField(item.updated_at),
    is_current: booleanField(item.is_current),
  };
}

export function parseUserSessionPage(value: unknown): UserSessionPage {
  const page = record(value);
  if (!Array.isArray(page.items)) return invalidResponse();
  return { items: page.items.map(parseUserSession) };
}

export function parseUserSessionProvision(value: unknown): UserSessionProvision {
  const item = record(value);
  const rotated = parseAccessTokenRotation(item);
  return {
    ...rotated,
    session: parseUserSession(item.session),
  };
}

export function parseModel(value: unknown): LocalModel {
  const item = record(value);
  if (!Array.isArray(item.capabilities)) return invalidResponse();
  return {
    model_id: stringField(item.model_id),
    display_name: stringField(item.display_name),
    runtime_id: stringField(item.runtime_id),
    modality: enumField(
      item.modality,
      ["text", "image", "audio", "multimodal"] as const,
    ),
    family: nullableString(item.family),
    parameter_class: nullableString(item.parameter_class),
    capabilities: item.capabilities.map((capability) =>
      enumField(capability, modelCapabilities),
    ),
    context_window: integerOrNull(item.context_window),
    quantization: nullableString(item.quantization),
    estimated_vram_bytes: integerOrNull(item.estimated_vram_bytes),
    availability: enumField(item.availability, modelAvailabilities),
    scale_class:
      item.scale_class === null
        ? null
        : enumField(item.scale_class, modelScaleClasses),
    required_vram_bytes: integerOrNull(item.required_vram_bytes),
    required_ram_bytes: integerOrNull(item.required_ram_bytes),
    installed: booleanField(item.installed),
    runnable_now: booleanField(item.runnable_now),
    future_capable: booleanField(item.future_capable),
    hardware_class:
      item.hardware_class === null
        ? null
        : enumField(item.hardware_class, hardwareClasses),
    fallback_model_id: nullableString(item.fallback_model_id),
  };
}

export function parseModelPage(value: unknown): LocalModelPage {
  const page = record(value);
  if (!Array.isArray(page.items)) return invalidResponse();
  return { items: page.items.map(parseModel) };
}

export function parseProductCapability(value: unknown): ProductCapability {
  const item = record(value);
  const status = enumField(item.status, ["available", "unavailable"] as const);
  if (!Array.isArray(item.blocking_reasons)) return invalidResponse();
  const blockingReasons = item.blocking_reasons.map((reason) =>
    enumField(reason, productCapabilityReasons),
  );
  if (
    blockingReasons.length > 3 ||
    new Set(blockingReasons).size !== blockingReasons.length ||
    (status === "available" && blockingReasons.length !== 0) ||
    (status === "unavailable" && blockingReasons.length === 0)
  ) {
    return invalidResponse();
  }
  return {
    id: enumField(item.id, productCapabilityIds),
    status,
    blocking_reasons: blockingReasons,
  };
}

export function parseProductCapabilityPage(
  value: unknown,
): ProductCapabilityPage {
  const page = record(value);
  if (!Array.isArray(page.items) || page.items.length !== 11) {
    return invalidResponse();
  }
  const items = page.items.map(parseProductCapability);
  if (new Set(items.map((item) => item.id)).size !== productCapabilityIds.length) {
    return invalidResponse();
  }
  return { items };
}

function boundedStringArray(value: unknown, maximum: number): string[] {
  if (!Array.isArray(value) || value.length > maximum) return invalidResponse();
  return value.map((item) => stringField(item));
}

export function parseProductFeature(value: unknown): ProductFeature {
  const item = record(value);
  const id = stringField(item.id);
  const uiEntryPoint = stringField(item.ui_entry_point);
  const backendCapability = stringField(item.backend_capability);
  const testCoverage = boundedStringArray(item.test_coverage, 8);
  if (
    !/^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$/.test(id) ||
    !/^\/[a-z0-9_/#-]+$/.test(uiEntryPoint) ||
    !/^[a-z][a-z0-9_]*$/.test(backendCapability) ||
    testCoverage.length === 0
  ) {
    return invalidResponse();
  }
  return {
    id,
    layer: enumField(item.layer, featureLayers),
    category: stringField(item.category),
    title: stringField(item.title),
    description: stringField(item.description),
    ui_entry_point: uiEntryPoint,
    backend_capability: backendCapability,
    required_permissions: boundedStringArray(item.required_permissions, 8),
    dependencies: boundedStringArray(item.dependencies, 8),
    status: enumField(item.status, featureStatuses),
    test_coverage: testCoverage,
  };
}

export function parseFeatureRegistry(value: unknown): FeatureRegistry {
  const registry = record(value);
  if (
    registry.schema_version !== 1 ||
    registry.product !== "AI OS" ||
    !Number.isSafeInteger(registry.count) ||
    !Array.isArray(registry.items) ||
    registry.items.length < 140 ||
    registry.items.length > 500 ||
    registry.count !== registry.items.length
  ) {
    return invalidResponse();
  }
  const items = registry.items.map(parseProductFeature);
  if (new Set(items.map((item) => item.id)).size !== items.length) {
    return invalidResponse();
  }
  return {
    schema_version: 1,
    product: "AI OS",
    count: registry.count as number,
    items,
  };
}

export function parseSystemDiagnostics(value: unknown): SystemDiagnostics {
  const snapshot = record(value);
  if (
    !Array.isArray(snapshot.services) ||
    snapshot.services.length !== diagnosticServiceIds.length ||
    !Array.isArray(snapshot.gpus) ||
    snapshot.gpus.length > 16
  ) {
    return invalidResponse();
  }
  const services = snapshot.services.map((rawService) => {
    const service = record(rawService);
    return {
      id: enumField(service.id, diagnosticServiceIds),
      status: enumField(service.status, diagnosticStatuses),
    };
  });
  if (
    new Set(services.map((service) => service.id)).size !==
    diagnosticServiceIds.length
  ) {
    return invalidResponse();
  }
  const gpus = snapshot.gpus.map((rawGpu) => {
    const gpu = record(rawGpu);
    const model = stringField(gpu.model);
    const vramBytes = integerOrNull(gpu.vram_bytes);
    if (
      model.length < 1 ||
      model.length > 96 ||
      vramBytes === null ||
      vramBytes < 1
    ) {
      return invalidResponse();
    }
    const freeVramBytes = gpu.free_vram_bytes === undefined
      ? null
      : integerOrNull(gpu.free_vram_bytes);
    if (freeVramBytes !== null && freeVramBytes < 0) return invalidResponse();
    return {
      model,
      vram_bytes: vramBytes,
      hardware_class: enumField(gpu.hardware_class, hardwareClasses),
      status: enumField(gpu.status, ["ready"] as const),
      ...(gpu.free_vram_bytes === undefined ? {} : { free_vram_bytes: freeVramBytes }),
      ...(gpu.vendor === undefined ? {} : { vendor: nullableString(gpu.vendor) }),
      ...(gpu.compute_capability === undefined ? {} : { compute_capability: nullableString(gpu.compute_capability) }),
      ...(gpu.driver_version === undefined ? {} : { driver_version: nullableString(gpu.driver_version) }),
      ...(gpu.runtime === undefined ? {} : { runtime: nullableString(gpu.runtime) }),
      ...(gpu.runtime_version === undefined ? {} : { runtime_version: nullableString(gpu.runtime_version) }),
    };
  });
  let hardware: HardwareDiagnostic | null | undefined;
  if (snapshot.hardware !== undefined && snapshot.hardware !== null) {
    const item = record(snapshot.hardware);
    const fingerprint = stringField(item.fingerprint);
    const profileGib = integerOrNull(item.profile_gib);
    const gpuCount = integerOrNull(item.gpu_count);
    const totalRam = integerOrNull(item.total_ram_bytes);
    const swapTotal = integerOrNull(item.swap_total_bytes);
    const swapFree = integerOrNull(item.swap_free_bytes);
    const cpuCount = integerOrNull(item.cpu_logical_count);
    if (
      !/^[a-f0-9]{64}$/.test(fingerprint) ||
      profileGib === null || profileGib < 0 ||
      gpuCount === null || gpuCount < 0 || gpuCount > 16 ||
      totalRam === null || totalRam < 1 ||
      swapTotal === null || swapTotal < 0 ||
      swapFree === null || swapFree < 0 || swapFree > swapTotal ||
      cpuCount === null || cpuCount < 1
    ) return invalidResponse();
    hardware = {
      fingerprint,
      profile_gib: profileGib,
      gpu_count: gpuCount,
      total_ram_bytes: totalRam,
      available_ram_bytes: integerOrNull(item.available_ram_bytes),
      swap_total_bytes: swapTotal,
      swap_free_bytes: swapFree,
      storage_total_bytes: integerOrNull(item.storage_total_bytes),
      storage_free_bytes: integerOrNull(item.storage_free_bytes),
      cpu_model: stringField(item.cpu_model),
      cpu_logical_count: cpuCount,
      os_name: stringField(item.os_name),
      os_version: stringField(item.os_version),
      architecture: stringField(item.architecture),
      upgrade_detected: booleanField(item.upgrade_detected),
      capability_cache_invalidated: booleanField(item.capability_cache_invalidated),
      restart_required: booleanField(item.restart_required),
      runtime_validated: booleanField(item.runtime_validated),
    };
  } else if (snapshot.hardware === null) {
    hardware = null;
  }
  const rawModels = snapshot.models === undefined ? [] : snapshot.models;
  const rawRoutes = snapshot.routes === undefined ? [] : snapshot.routes;
  if (!Array.isArray(rawModels) || rawModels.length > 256 || !Array.isArray(rawRoutes) || rawRoutes.length > 20) {
    return invalidResponse();
  }
  const models = rawModels.map((rawModel) => {
    const item = record(rawModel);
    if (!Array.isArray(item.reasons) || item.reasons.length < 1 || item.reasons.length > 4) return invalidResponse();
    return {
      model_id: stringField(item.model_id),
      display_name: stringField(item.display_name),
      runtime_id: stringField(item.runtime_id),
      status: enumField(item.status, modelEligibilityStatuses),
      reasons: item.reasons.map((reason) => enumField(reason, modelAdmissionReasons)),
      performance: enumField(item.performance, performanceClasses),
      verified: booleanField(item.verified),
      fallback_model_id: nullableString(item.fallback_model_id),
    };
  });
  const routes = rawRoutes.map((rawRoute) => {
    const item = record(rawRoute);
    if (!Array.isArray(item.fallback_model_ids) || item.fallback_model_ids.length > 32) return invalidResponse();
    return {
      task: enumField(item.task, modelTasks),
      model_id: stringField(item.model_id),
      fallback_model_ids: item.fallback_model_ids.map(stringField),
      inference_mode: enumField(item.inference_mode, ["auto", "thinking_disabled"] as const),
    };
  });
  const rawProviders = snapshot.external_providers === undefined ? [] : snapshot.external_providers;
  const rawSecurityEvents = snapshot.security_events === undefined ? [] : snapshot.security_events;
  if (
    !Array.isArray(rawProviders) || rawProviders.length > 16 ||
    !Array.isArray(rawSecurityEvents) || rawSecurityEvents.length > 100
  ) return invalidResponse();
  const externalProviders = rawProviders.map((rawProvider) => {
    const provider = record(rawProvider);
    const spent = integerOrNull(provider.spent_micros);
    const limit = integerOrNull(provider.spending_limit_micros);
    const quota = integerOrNull(provider.quota_remaining_tokens);
    const verified = integerOrNull(provider.verified_model_count);
    if (
      spent === null || spent < 0 || limit === null || limit < 0 ||
      (quota !== null && quota < 0) || verified === null || verified < 0 || verified > 64
    ) return invalidResponse();
    return {
      provider_id: stringField(provider.provider_id),
      status: enumField(provider.status, externalProviderStatuses),
      spent_micros: spent,
      spending_limit_micros: limit,
      quota_remaining_tokens: quota,
      verified_model_count: verified,
    };
  });
  let agents: AgentRuntimeDiagnostic | null | undefined;
  if (snapshot.agents === null) {
    agents = null;
  } else if (snapshot.agents !== undefined) {
    const item = record(snapshot.agents);
    const active = integerOrNull(item.active_count);
    const retained = integerOrNull(item.retained_count);
    const rawStatuses = record(item.statuses);
    const statuses: Partial<Record<AgentRuntimeStatus, number>> = {};
    for (const [rawStatus, rawCount] of Object.entries(rawStatuses)) {
      const status = enumField(rawStatus, agentRuntimeStatuses);
      const count = integerOrNull(rawCount);
      if (count === null || count < 0 || count > 100) return invalidResponse();
      statuses[status] = count;
    }
    if (active === null || active < 0 || active > 8 || retained === null || retained < 0 || retained > 100) return invalidResponse();
    agents = { active_count: active, retained_count: retained, statuses };
  }
  let selfUpdate: SystemDiagnostics["self_update"];
  if (snapshot.self_update !== undefined) {
    const item = record(snapshot.self_update);
    selfUpdate = {
      configured: booleanField(item.configured),
      status: enumField(item.status, selfUpdateStates),
      checkpoint_ready: booleanField(item.checkpoint_ready),
      rollback_ready: booleanField(item.rollback_ready),
    };
  }
  const securityEvents = rawSecurityEvents.map((rawEvent) => {
    const item = record(rawEvent);
    return {
      kind: enumField(item.kind, securityEventKinds),
      occurred_at: stringField(item.occurred_at),
    };
  });
  return {
    mode: enumField(snapshot.mode, ["local", "remote"] as const),
    services,
    gpus,
    ...(hardware === undefined ? {} : { hardware }),
    ...(snapshot.models === undefined ? {} : { models }),
    ...(snapshot.routes === undefined ? {} : { routes }),
    ...(snapshot.external_providers === undefined ? {} : { external_providers: externalProviders }),
    ...(snapshot.agents === undefined ? {} : { agents }),
    ...(snapshot.self_update === undefined ? {} : { self_update: selfUpdate }),
    ...(snapshot.security_events === undefined ? {} : { security_events: securityEvents }),
  };
}

function boundedNumber(value: unknown, minimum: number, maximum: number): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < minimum || value > maximum) {
    return invalidResponse();
  }
  return value;
}

export function parseExternalAISettings(value: unknown): ExternalAISettings {
  const settings = record(value);
  if (
    !Array.isArray(settings.providers) || settings.providers.length > 16 ||
    !Array.isArray(settings.supported_provider_kinds) ||
    settings.supported_provider_kinds.length !== externalProviderKinds.length
  ) return invalidResponse();
  const supportedKinds = settings.supported_provider_kinds.map((kind) =>
    enumField(kind, externalProviderKinds));
  if (new Set(supportedKinds).size !== externalProviderKinds.length) return invalidResponse();
  const providers = settings.providers.map((rawProvider) => {
    const provider = record(rawProvider);
    if (!Array.isArray(provider.models) || provider.models.length > 64) return invalidResponse();
    const models = provider.models.map((rawModel) => {
      const model = record(rawModel);
      if (!Array.isArray(model.tasks) || model.tasks.length < 1 || model.tasks.length > 16) return invalidResponse();
      const tasks = model.tasks.map((task) => enumField(task, modelTasks));
      if (new Set(tasks).size !== tasks.length) return invalidResponse();
      const contextWindow = integerOrNull(model.context_window);
      const inputCost = integerOrNull(model.input_cost_micros_per_million_tokens);
      const outputCost = integerOrNull(model.output_cost_micros_per_million_tokens);
      if (
        contextWindow === null || contextWindow < 0 || contextWindow > 10_000_000 ||
        inputCost === null || inputCost < 0 ||
        outputCost === null || outputCost < 0
      ) return invalidResponse();
      return {
        model_id: stringField(model.model_id),
        tasks,
        verified: booleanField(model.verified),
        verification_evidence_sha256: nullableString(model.verification_evidence_sha256),
        measured_quality: boundedNumber(model.measured_quality, 0, 100),
        measured_latency_ms: boundedNumber(model.measured_latency_ms, 0, 3_600_000),
        stability_rate: boundedNumber(model.stability_rate, 0, 1),
        context_window: contextWindow,
        input_cost_micros_per_million_tokens: inputCost,
        output_cost_micros_per_million_tokens: outputCost,
      };
    });
    const priority = integerOrNull(provider.priority);
    const rateLimit = integerOrNull(provider.rate_limit_requests_per_minute);
    const spendingLimit = integerOrNull(provider.spending_limit_micros);
    const spent = integerOrNull(provider.spent_micros);
    const quota = integerOrNull(provider.quota_remaining_tokens);
    if (
      priority === null || priority < 0 || priority > 1_000 ||
      rateLimit === null || rateLimit < 1 || rateLimit > 1_000 ||
      spendingLimit === null || spendingLimit < 0 ||
      spent === null || spent < 0 ||
      (quota !== null && quota < 0)
    ) return invalidResponse();
    return {
      provider_id: stringField(provider.provider_id),
      kind: enumField(provider.kind, externalProviderKinds),
      enabled: booleanField(provider.enabled),
      key_configured: booleanField(provider.key_configured),
      free_tier: booleanField(provider.free_tier),
      priority,
      timeout_seconds: boundedNumber(provider.timeout_seconds, 1, 60),
      rate_limit_requests_per_minute: rateLimit,
      spending_limit_micros: spendingLimit,
      spent_micros: spent,
      quota_remaining_tokens: quota,
      status: enumField(provider.status, externalProviderStatuses),
      models,
    };
  });
  if (new Set(providers.map((provider) => provider.provider_id)).size !== providers.length) return invalidResponse();
  return {
    configured: booleanField(settings.configured),
    global_enabled: booleanField(settings.global_enabled),
    providers,
    supported_provider_kinds: supportedKinds,
  };
}

export function parseSelfUpdateStatus(value: unknown): SelfUpdateStatus {
  const item = record(value);
  if (!Array.isArray(item.gates) || item.gates.length > 32) return invalidResponse();
  const gates = item.gates.map((rawGate) => {
    const gate = record(rawGate);
    const name = stringField(gate.name);
    if (!/^[a-z][a-z0-9_-]{0,63}$/.test(name)) return invalidResponse();
    return { name, passed: booleanField(gate.passed) };
  });
  if (new Set(gates.map((gate) => gate.name)).size !== gates.length) return invalidResponse();
  const version = nullableString(item.version);
  const candidateCommit = nullableString(item.candidate_commit);
  const failureCode = nullableString(item.failure_code);
  if (
    (version !== null && (version.length < 1 || version.length > 64)) ||
    (candidateCommit !== null && !/^[a-f0-9]{40}$/.test(candidateCommit)) ||
    (failureCode !== null && failureCode.length > 96)
  ) return invalidResponse();
  return {
    configured: booleanField(item.configured),
    status: enumField(item.status, selfUpdateStates),
    version,
    candidate_commit: candidateCommit,
    checkpoint_ready: booleanField(item.checkpoint_ready),
    rollback_ready: booleanField(item.rollback_ready),
    activation_requires_owner: booleanField(item.activation_requires_owner),
    gates,
    failure_code: failureCode,
  };
}

export function parseAgentOSCapabilities(value: unknown): AgentOSCapabilities {
  const item = record(value);
  if (!Array.isArray(item.profiles) || item.profiles.length !== agentKinds.length) return invalidResponse();
  const profiles = item.profiles.map((rawProfile) => {
    const profile = record(rawProfile);
    if (!Array.isArray(profile.permissions) || profile.permissions.length > agentPermissions.length) return invalidResponse();
    const permissions = profile.permissions.map((permission) => enumField(permission, agentPermissions));
    if (new Set(permissions).size !== permissions.length) return invalidResponse();
    return {
      kind: enumField(profile.kind, agentKinds),
      permissions,
      registered: booleanField(profile.registered),
    };
  });
  if (new Set(profiles.map((profile) => profile.kind)).size !== agentKinds.length) return invalidResponse();
  const maxRetries = integerOrNull(item.max_retries);
  const maxDeadline = integerOrNull(item.max_deadline_seconds);
  const activeRuns = integerOrNull(item.active_runs);
  const maxConcurrency = integerOrNull(item.max_concurrency);
  if (
    maxRetries === null || maxRetries < 0 || maxRetries > 2 ||
    maxDeadline === null || maxDeadline < 1 || maxDeadline > 600 ||
    activeRuns === null || activeRuns < 0 || activeRuns > 8 ||
    maxConcurrency === null || maxConcurrency < 1 || maxConcurrency > 8
  ) return invalidResponse();
  return {
    profiles,
    max_retries: maxRetries,
    max_deadline_seconds: maxDeadline,
    active_runs: activeRuns,
    max_concurrency: maxConcurrency,
    persistence: stringField(item.persistence),
  };
}

export function parseAgentRun(value: unknown): AgentRun {
  const item = record(value);
  if (!Array.isArray(item.attempts) || item.attempts.length > 48) return invalidResponse();
  const attempts = item.attempts.map((rawAttempt) => {
    const attempt = record(rawAttempt);
    if (!Array.isArray(attempt.checks) || attempt.checks.length < 1 || attempt.checks.length > 128) return invalidResponse();
    const checks = attempt.checks.map((rawCheck) => {
      const check = record(rawCheck);
      const evidence = nullableString(check.evidence_sha256);
      if (evidence !== null && !/^[a-f0-9]{64}$/.test(evidence)) return invalidResponse();
      return {
        check_id: stringField(check.check_id),
        passed: booleanField(check.passed),
        failure: stringField(check.failure),
        evidence_sha256: evidence,
      };
    });
    const attemptNumber = integerOrNull(attempt.attempt);
    const modelId = nullableString(attempt.model_id);
    const outputDigest = stringField(attempt.output_sha256);
    if (
      attemptNumber === null || attemptNumber < 1 || attemptNumber > 3 ||
      (modelId !== null && !/^[a-z0-9][a-z0-9_-]{0,63}:[a-f0-9]{24}$/.test(modelId)) ||
      !/^[a-f0-9]{64}$/.test(outputDigest)
    ) return invalidResponse();
    return {
      step_id: stringField(attempt.step_id),
      attempt: attemptNumber,
      agent: enumField(attempt.agent, agentKinds),
      model_id: modelId,
      verified: booleanField(attempt.verified),
      output_sha256: outputDigest,
      checks,
    };
  });
  return {
    id: stringField(item.id),
    task: enumField(item.task, modelTasks),
    specialist: item.specialist === null ? null : enumField(item.specialist, agentKinds),
    status: enumField(item.status, agentRuntimeStatuses),
    created_at: stringField(item.created_at),
    updated_at: stringField(item.updated_at),
    output: nullableString(item.output),
    failure_code: nullableString(item.failure_code),
    attempts,
  };
}

export function parseAgentRunPage(value: unknown): AgentRunPage {
  const item = record(value);
  if (!Array.isArray(item.items) || item.items.length > 100) return invalidResponse();
  return { items: item.items.map(parseAgentRun) };
}

export function parseConversation(value: unknown): ConversationSummary {
  const item = record(value);
  return {
    id: stringField(item.id),
    title: nullableString(item.title),
    is_pinned: booleanField(item.is_pinned),
    is_archived: booleanField(item.is_archived),
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
    provenance_kind: enumField(item.provenance_kind, [
      "upload",
      "image_generation",
      "image_editing",
      "speech_synthesis",
    ] as const),
    source_asset_id: nullableString(item.source_asset_id),
    runtime_id: nullableString(item.runtime_id),
    model_id: nullableString(item.model_id),
    created_at: stringField(item.created_at),
    deleted_at: nullableString(item.deleted_at),
  };
}

export function parseVoiceTranscription(value: unknown): VoiceTranscription {
  const item = record(value);
  if (
    typeof item.duration_seconds !== "number" ||
    item.duration_seconds <= 0 ||
    !Number.isFinite(item.duration_seconds)
  ) return invalidResponse();
  const duration = item.duration_seconds;
  const text = stringField(item.text);
  if (!text.trim()) return invalidResponse();
  return {
    text,
    language: nullableString(item.language),
    duration_seconds: duration,
  };
}

export function parseVoiceSynthesis(value: unknown): VoiceSynthesis {
  const item = record(value);
  return {
    asset: parseAsset(item.asset),
    created: booleanField(item.created),
  };
}

export function parseImageOperation(value: unknown): ImageOperation {
  const item = record(value);
  return {
    asset: parseAsset(item.asset),
    message: parseMessage(item.message),
    created: booleanField(item.created),
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
  const provenanceKind =
    item.provenance_kind === null
      ? null
      : enumField(item.provenance_kind, [
          "upload",
          "image_generation",
          "image_editing",
          "speech_synthesis",
        ] as const);
  const sourceAssetId = nullableString(item.source_asset_id);
  if (position === null || position < 1) return invalidResponse();
  if (
    (state === "deleted" &&
      (originalFilename !== null ||
        mediaType !== null ||
        byteSize !== null ||
        provenanceKind !== null ||
        sourceAssetId !== null)) ||
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
    provenance_kind: provenanceKind,
    source_asset_id: sourceAssetId,
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
    initiator: enumField(
      item.initiator,
      ["explicit_user", "workflow"] as const,
    ),
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

const workflowStatuses = [
  "pending",
  "running",
  "completed",
  "failed",
  "cancelled",
  "timed_out",
] as const;

export function parseWorkflowStep(value: unknown): WorkflowStep {
  const item = record(value);
  const position = integerOrNull(item.position);
  const status = enumField(item.status, workflowStatuses);
  const argumentsValue = jsonValue(item.arguments);
  const result = jsonValue(item.result);
  const toolExecutionId = nullableString(item.tool_execution_id);
  const errorCode = nullableString(item.error_code);
  const startedAt = nullableString(item.started_at);
  const completedAt = nullableString(item.completed_at);
  const duration = integerOrNull(item.duration_ms);
  if (
    position === null || position < 1 || position > 8 ||
    argumentsValue === null || Array.isArray(argumentsValue) ||
    typeof argumentsValue !== "object" ||
    (duration !== null && duration < 0) ||
    (status === "pending" &&
      (startedAt !== null || completedAt !== null || toolExecutionId !== null ||
        result !== null || errorCode !== null || duration !== null)) ||
    (status === "running" &&
      (startedAt === null || completedAt !== null || result !== null ||
        errorCode !== null || duration !== null)) ||
    (status === "completed" &&
      (startedAt === null || completedAt === null || toolExecutionId === null ||
        result === null || errorCode !== null || duration === null)) ||
    (["failed", "cancelled", "timed_out"] as const).includes(
      status as "failed" | "cancelled" | "timed_out",
    ) && (completedAt === null || result !== null || errorCode === null || duration === null)
  ) {
    return invalidResponse();
  }
  return {
    id: stringField(item.id),
    position,
    tool_name: stringField(item.tool_name),
    permission: stringField(item.permission),
    arguments: argumentsValue,
    status,
    tool_execution_id: toolExecutionId,
    result,
    error_code: errorCode,
    started_at: startedAt,
    completed_at: completedAt,
    duration_ms: duration,
  };
}

export function parseWorkflow(value: unknown): Workflow {
  const item = record(value);
  const status = enumField(item.status, workflowStatuses);
  const stepCount = integerOrNull(item.step_count);
  const current = integerOrNull(item.current_step_position);
  const result = jsonValue(item.result);
  const errorCode = nullableString(item.error_code);
  const startedAt = nullableString(item.started_at);
  const completedAt = nullableString(item.completed_at);
  if (!Array.isArray(item.steps) || typeof item.cancel_requested !== "boolean") {
    return invalidResponse();
  }
  const steps = item.steps.map(parseWorkflowStep);
  if (
    stepCount === null || stepCount < 1 || stepCount > 8 ||
    steps.length !== stepCount ||
    steps.some((step, index) => step.position !== index + 1) ||
    (current !== null && (current < 1 || current > stepCount)) ||
    (status === "pending" &&
      (startedAt !== null || completedAt !== null || current !== null ||
        result !== null || errorCode !== null || item.cancel_requested)) ||
    (status === "running" &&
      (startedAt === null || completedAt !== null || current === null ||
        result !== null || errorCode !== null)) ||
    (status === "completed" &&
      (startedAt === null || completedAt === null || current === null ||
        result === null || errorCode !== null || item.cancel_requested)) ||
    (["failed", "cancelled", "timed_out"] as const).includes(
      status as "failed" | "cancelled" | "timed_out",
    ) && (completedAt === null || result !== null || errorCode === null)
  ) {
    return invalidResponse();
  }
  return {
    id: stringField(item.id),
    name: nullableString(item.name),
    status,
    step_count: stepCount,
    current_step_position: current,
    cancel_requested: item.cancel_requested,
    result,
    error_code: errorCode,
    created_at: stringField(item.created_at),
    updated_at: stringField(item.updated_at),
    started_at: startedAt,
    completed_at: completedAt,
    steps,
  };
}

export function parseWorkflowPage(value: unknown): WorkflowPage {
  const page = record(value);
  if (!Array.isArray(page.items)) return invalidResponse();
  return { items: page.items.map(parseWorkflow) };
}
