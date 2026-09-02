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

export type ConnectorKind = "rest" | "webhook" | "local_api";
export type ConnectorAuthKind = "none" | "bearer" | "api_key" | "oauth2_bearer";
export type ConnectorConnectionStatus =
  | "revoked"
  | "disabled"
  | "ready"
  | "healthy"
  | "unavailable";
export type ConnectorExecutionStatus =
  | "completed"
  | "failed"
  | "timed_out"
  | "rate_limited";

export interface ConnectorSettings {
  configured: boolean;
  allowed_origins: string[];
  supported_kinds: ConnectorKind[];
  supported_auth_kinds: ConnectorAuthKind[];
}

export interface Connector {
  id: UUID;
  name: string;
  kind: ConnectorKind;
  base_url: string;
  auth_kind: ConnectorAuthKind;
  credential_configured: boolean;
  scopes: Array<"read" | "write">;
  path_prefixes: string[];
  health_path: string;
  enabled: boolean;
  connection_status: ConnectorConnectionStatus;
  timeout_seconds: number;
  max_retries: number;
  rate_limit_requests_per_minute: number;
  last_health_checked_at: Timestamp | null;
  created_at: Timestamp;
  updated_at: Timestamp;
  revoked_at: Timestamp | null;
}

export interface ConnectorPage { items: Connector[]; }

export interface ConnectorWriteRequest {
  name: string;
  kind: ConnectorKind;
  base_url: string;
  auth_kind: ConnectorAuthKind;
  credential?: string;
  scopes: Array<"read" | "write">;
  path_prefixes: string[];
  health_path: string;
  enabled: boolean;
  timeout_seconds?: number;
  max_retries?: number;
  rate_limit_requests_per_minute?: number;
}

export interface ConnectorExecutionRequest {
  method: "GET" | "HEAD" | "POST" | "PUT" | "PATCH" | "DELETE";
  path: string;
  json_body?: JsonValue;
  idempotency_key?: string;
}

export interface ConnectorExecution {
  id: UUID;
  connector_id: UUID;
  action: "execute" | "health";
  method: string;
  path: string;
  status: ConnectorExecutionStatus;
  attempts: number;
  response_status_code: number | null;
  request_body_sha256: string | null;
  response_body_sha256: string | null;
  response_bytes: number | null;
  error_code: string | null;
  started_at: Timestamp;
  completed_at: Timestamp;
  duration_ms: number;
}

export interface ConnectorExecutionResult {
  execution: ConnectorExecution;
  payload: JsonValue;
}

export interface ConnectorExecutionPage { items: ConnectorExecution[]; }

export type MarketingCampaignStatus =
  | "pending"
  | "running"
  | "needs_approval"
  | "publishing"
  | "awaiting_analytics"
  | "completed"
  | "failed"
  | "cancelled"
  | "timed_out";
export type MarketingStageKind =
  | "research"
  | "strategy"
  | "content"
  | "creative"
  | "approval"
  | "publish"
  | "analytics"
  | "optimization";
export type MarketingStageStatus =
  | "pending"
  | "running"
  | "blocked"
  | "completed"
  | "failed"
  | "cancelled";
export type MarketingChannel = "email" | "social" | "search" | "web";

export interface MarketingSourceFact {
  source_reference: string;
  fact: string;
}

export interface MarketingCampaignCreateRequest {
  name: string;
  objective: string;
  product: string;
  audience: string;
  channels: MarketingChannel[];
  source_facts: MarketingSourceFact[];
  publisher_connector_id?: UUID;
  publish_path?: string;
}

export interface MarketingAnalyticsRequest {
  source_reference: string;
  observed_at: Timestamp;
  impressions: number;
  clicks: number;
  conversions: number;
  spend_minor: number;
  revenue_minor: number;
}

export interface MarketingStage {
  id: UUID;
  position: number;
  kind: MarketingStageKind;
  status: MarketingStageStatus;
  output: string | null;
  output_sha256: string | null;
  model_id: string | null;
  connector_execution_id: UUID | null;
  error_code: string | null;
  started_at: Timestamp | null;
  completed_at: Timestamp | null;
  duration_ms: number | null;
}

export interface MarketingCampaign {
  id: UUID;
  name: string;
  objective: string;
  product: string;
  audience: string;
  channels: MarketingChannel[];
  source_facts: MarketingSourceFact[];
  publisher_connector_id: UUID | null;
  publish_path: string | null;
  status: MarketingCampaignStatus;
  current_stage: MarketingStageKind | null;
  analytics: JsonValue | null;
  error_code: string | null;
  created_at: Timestamp;
  updated_at: Timestamp;
  started_at: Timestamp | null;
  approved_at: Timestamp | null;
  published_at: Timestamp | null;
  completed_at: Timestamp | null;
  stages: MarketingStage[];
}

export interface MarketingCampaignPage { items: MarketingCampaign[]; }

export type MarketAssetClass = "indian_stock" | "global_stock" | "crypto" | "fx";
export type FinanceArtifactKind = "research" | "strategy" | "backtest" | "portfolio" | "risk" | "journal";
export type PaperOrderSide = "buy" | "sell";
export type PaperOrderStatus = "executed" | "rejected";
export type MarketAlertCondition = "at_or_above" | "at_or_below";
export type MarketAlertStatus = "active" | "triggered" | "cancelled";

export interface FinanceWorkspaceCreateRequest {
  name: string;
  base_currency: string;
  initial_cash_minor: number;
  max_order_bps: number;
  max_position_bps: number;
}

export interface MarketWatchItemRequest {
  asset_class: MarketAssetClass;
  symbol: string;
  display_name: string;
}

export interface MarketResearchRequest {
  kind: "research" | "strategy";
  asset_class: MarketAssetClass;
  subject: string;
  source_reference: string;
  source_facts: MarketingSourceFact[];
}

export interface MarketBarRequest { observed_at: Timestamp; close_minor: number; }

export interface BacktestRequest {
  asset_class: MarketAssetClass;
  symbol: string;
  source_reference: string;
  bars: MarketBarRequest[];
  fast_window: number;
  slow_window: number;
  initial_cash_minor: number;
  fee_bps: number;
}

export interface PaperOrderRequest {
  execution_mode: "paper";
  asset_class: MarketAssetClass;
  symbol: string;
  side: PaperOrderSide;
  quantity_micros: number;
  price_minor: number;
  observed_at: Timestamp;
  source_reference: string;
  owner_confirmed: boolean;
}

export interface MarketQuoteRequest {
  asset_class: MarketAssetClass;
  symbol: string;
  price_minor: number;
  observed_at: Timestamp;
  source_reference: string;
}

export interface PortfolioAnalysisRequest {
  source_reference: string;
  quotes: MarketQuoteRequest[];
}

export interface MarketAlertRequest {
  asset_class: MarketAssetClass;
  symbol: string;
  condition: MarketAlertCondition;
  threshold_minor: number;
}

export interface TradingJournalRequest {
  title: string;
  note: string;
  source_reference: string;
}

export interface MarketWatchItem {
  id: UUID;
  asset_class: MarketAssetClass;
  symbol: string;
  display_name: string;
  created_at: Timestamp;
}

export interface PaperPosition {
  id: UUID;
  asset_class: MarketAssetClass;
  symbol: string;
  quantity_micros: number;
  cost_basis_minor: number;
  updated_at: Timestamp;
}

export interface PaperOrder {
  id: UUID;
  asset_class: MarketAssetClass;
  symbol: string;
  side: PaperOrderSide;
  quantity_micros: number;
  price_minor: number;
  notional_minor: number;
  source_reference: string;
  observed_at: Timestamp;
  status: PaperOrderStatus;
  rejection_code: string | null;
  cash_after_minor: number;
  created_at: Timestamp;
  execution_mode: "paper";
}

export interface MarketAlert {
  id: UUID;
  asset_class: MarketAssetClass;
  symbol: string;
  condition: MarketAlertCondition;
  threshold_minor: number;
  status: MarketAlertStatus;
  last_price_minor: number | null;
  last_source_reference: string | null;
  last_observed_at: Timestamp | null;
  created_at: Timestamp;
  triggered_at: Timestamp | null;
}

export interface FinanceArtifact {
  id: UUID;
  kind: FinanceArtifactKind;
  title: string;
  source_reference: string;
  input_sha256: string;
  output: string;
  output_sha256: string;
  model_id: string;
  duration_ms: number;
  created_at: Timestamp;
}

export interface FinanceWorkspace {
  id: UUID;
  name: string;
  base_currency: string;
  initial_cash_minor: number;
  cash_minor: number;
  max_order_bps: number;
  max_position_bps: number;
  created_at: Timestamp;
  updated_at: Timestamp;
  watch_items: MarketWatchItem[];
  positions: PaperPosition[];
  orders: PaperOrder[];
  alerts: MarketAlert[];
  artifacts: FinanceArtifact[];
  execution_mode: "paper";
  live_broker_status: "external_dependency";
}

export interface FinanceWorkspacePage { items: FinanceWorkspace[]; }
export interface PortfolioAnalysis { portfolio: FinanceArtifact; risk: FinanceArtifact; }
export interface MarketAlertEvaluation { items: MarketAlert[]; }

export type LearningProgramStatus = "active" | "completed" | "archived";
export type LearningLessonStatus = "planned" | "ready" | "completed";
export type LearningActivityKind = "exercise" | "quiz" | "conversation" | "revision";

export interface LearningProgramCreateRequest {
  subject: string;
  goal: string;
  target_language: string;
  instruction_language: string;
  start_difficulty: number;
  target_difficulty: number;
  weekly_minutes: number;
  adaptive_difficulty: boolean;
}

export interface LearningActivityCreateRequest {
  kind: LearningActivityKind;
  prompt: string;
  expected_answer: string;
  explanation: string;
  difficulty: number;
  max_attempts: number;
}

export interface LearningAttemptRequest { answer: string; }
export interface LearningReviewItemCreateRequest { front: string; back: string; }
export interface LearningReviewRequest { quality: number; }

export interface LearningAttempt {
  id: UUID;
  activity_id: UUID;
  is_correct: boolean;
  score_bps: number;
  feedback: string;
  created_at: Timestamp;
}

export interface LearningActivity {
  id: UUID;
  lesson_id: UUID;
  kind: LearningActivityKind;
  prompt: string;
  explanation_available_after_attempt: true;
  difficulty: number;
  max_attempts: number;
  attempts: LearningAttempt[];
  created_at: Timestamp;
}

export interface LearningLesson {
  id: UUID;
  position: number;
  title: string;
  objectives: string[];
  difficulty: number;
  status: LearningLessonStatus;
  content: string | null;
  output_sha256: string | null;
  model_id: string | null;
  memory_context_count: number;
  score_bps: number | null;
  activities: LearningActivity[];
  created_at: Timestamp;
  generated_at: Timestamp | null;
  completed_at: Timestamp | null;
}

export interface LearningReviewItem {
  id: UUID;
  front: string;
  back: string;
  interval_days: number;
  ease_milli: number;
  repetitions: number;
  due_at: Timestamp;
  last_quality: number | null;
  created_at: Timestamp;
  updated_at: Timestamp;
}

export interface LearningProgram {
  id: UUID;
  subject: string;
  goal: string;
  target_language: string;
  instruction_language: string;
  start_difficulty: number;
  current_difficulty: number;
  target_difficulty: number;
  weekly_minutes: number;
  adaptive_difficulty: boolean;
  status: LearningProgramStatus;
  total_lessons: number;
  completed_lessons: number;
  total_attempts: number;
  correct_attempts: number;
  progress_bps: number;
  accuracy_bps: number | null;
  lessons: LearningLesson[];
  review_items: LearningReviewItem[];
  created_at: Timestamp;
  updated_at: Timestamp;
  completed_at: Timestamp | null;
}

export interface LearningProgramPage { items: LearningProgram[]; }

export interface LearningCapabilities {
  teacher_mode: true;
  speaking_partner: true;
  exam_mode: true;
  vocabulary_trainer: true;
  spaced_repetition: true;
  pronunciation_scoring: false;
  pronunciation_status: "external_dependency";
  pronunciation_dependencies: ["pronunciation_scoring_provider"];
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

export type AgentInputSource = "text" | "voice";

export interface AgentPlanStep {
  step_id: string;
  agent: AgentKind;
  task: ModelTask;
  permissions: AgentPermission[];
  requires_objective_evidence: boolean;
}

export interface AgentRunEvent {
  sequence: number;
  status: AgentRuntimeStatus;
  created_at: string;
  step_id: string | null;
  attempt: number | null;
  agent: AgentKind | null;
  model_id: string | null;
}

export interface AgentRun {
  id: string;
  goal: string;
  source: AgentInputSource;
  task: ModelTask;
  specialist: AgentKind | null;
  status: AgentRuntimeStatus;
  created_at: string;
  updated_at: string;
  output: string | null;
  failure_code: string | null;
  plan: AgentPlanStep[];
  events: AgentRunEvent[];
  attempts: AgentAttempt[];
}

export interface AgentRunPage { items: AgentRun[]; }

export interface AgentRunCreateRequest {
  goal: string;
  task: ModelTask;
  source?: AgentInputSource;
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
const agentInputSources = ["text", "voice"] as const;
const connectorKinds = ["rest", "webhook", "local_api"] as const;
const connectorAuthKinds = ["none", "bearer", "api_key", "oauth2_bearer"] as const;
const connectorConnectionStatuses = ["revoked", "disabled", "ready", "healthy", "unavailable"] as const;
const connectorExecutionStatuses = ["completed", "failed", "timed_out", "rate_limited"] as const;
const marketingCampaignStatuses = ["pending", "running", "needs_approval", "publishing", "awaiting_analytics", "completed", "failed", "cancelled", "timed_out"] as const;
const marketingStageKinds = ["research", "strategy", "content", "creative", "approval", "publish", "analytics", "optimization"] as const;
const marketingStageStatuses = ["pending", "running", "blocked", "completed", "failed", "cancelled"] as const;
const marketingChannels = ["email", "social", "search", "web"] as const;
const marketAssetClasses = ["indian_stock", "global_stock", "crypto", "fx"] as const;
const financeArtifactKinds = ["research", "strategy", "backtest", "portfolio", "risk", "journal"] as const;
const paperOrderSides = ["buy", "sell"] as const;
const paperOrderStatuses = ["executed", "rejected"] as const;
const marketAlertConditions = ["at_or_above", "at_or_below"] as const;
const marketAlertStatuses = ["active", "triggered", "cancelled"] as const;
const learningProgramStatuses = ["active", "completed", "archived"] as const;
const learningLessonStatuses = ["planned", "ready", "completed"] as const;
const learningActivityKinds = ["exercise", "quiz", "conversation", "revision"] as const;
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

export function parseConnectorSettings(value: unknown): ConnectorSettings {
  const item = record(value);
  if (
    !Array.isArray(item.allowed_origins) || item.allowed_origins.length > 64 ||
    !Array.isArray(item.supported_kinds) || item.supported_kinds.length !== connectorKinds.length ||
    !Array.isArray(item.supported_auth_kinds) || item.supported_auth_kinds.length !== connectorAuthKinds.length
  ) return invalidResponse();
  const allowedOrigins = item.allowed_origins.map(stringField);
  const supportedKinds = item.supported_kinds.map((kind) => enumField(kind, connectorKinds));
  const supportedAuthKinds = item.supported_auth_kinds.map((kind) => enumField(kind, connectorAuthKinds));
  if (
    new Set(allowedOrigins).size !== allowedOrigins.length ||
    new Set(supportedKinds).size !== connectorKinds.length ||
    new Set(supportedAuthKinds).size !== connectorAuthKinds.length
  ) return invalidResponse();
  return {
    configured: booleanField(item.configured),
    allowed_origins: allowedOrigins,
    supported_kinds: supportedKinds,
    supported_auth_kinds: supportedAuthKinds,
  };
}

export function parseConnector(value: unknown): Connector {
  const item = record(value);
  if (
    !Array.isArray(item.scopes) || item.scopes.length < 1 || item.scopes.length > 2 ||
    !Array.isArray(item.path_prefixes) || item.path_prefixes.length < 1 || item.path_prefixes.length > 16
  ) return invalidResponse();
  const scopes = item.scopes.map((scope) => enumField(scope, ["read", "write"] as const));
  const pathPrefixes = item.path_prefixes.map(stringField);
  const timeout = integerOrNull(item.timeout_seconds);
  const retries = integerOrNull(item.max_retries);
  const rateLimit = integerOrNull(item.rate_limit_requests_per_minute);
  if (
    new Set(scopes).size !== scopes.length ||
    new Set(pathPrefixes).size !== pathPrefixes.length ||
    timeout === null || timeout < 1 || timeout > 10 ||
    retries === null || retries < 0 || retries > 2 ||
    rateLimit === null || rateLimit < 1 || rateLimit > 600
  ) return invalidResponse();
  return {
    id: stringField(item.id),
    name: stringField(item.name),
    kind: enumField(item.kind, connectorKinds),
    base_url: stringField(item.base_url),
    auth_kind: enumField(item.auth_kind, connectorAuthKinds),
    credential_configured: booleanField(item.credential_configured),
    scopes,
    path_prefixes: pathPrefixes,
    health_path: stringField(item.health_path),
    enabled: booleanField(item.enabled),
    connection_status: enumField(item.connection_status, connectorConnectionStatuses),
    timeout_seconds: timeout,
    max_retries: retries,
    rate_limit_requests_per_minute: rateLimit,
    last_health_checked_at: nullableString(item.last_health_checked_at),
    created_at: stringField(item.created_at),
    updated_at: stringField(item.updated_at),
    revoked_at: nullableString(item.revoked_at),
  };
}

export function parseConnectorPage(value: unknown): ConnectorPage {
  const item = record(value);
  if (!Array.isArray(item.items) || item.items.length > 100) return invalidResponse();
  return { items: item.items.map(parseConnector) };
}

export function parseConnectorExecution(value: unknown): ConnectorExecution {
  const item = record(value);
  const status = enumField(item.status, connectorExecutionStatuses);
  const attempts = integerOrNull(item.attempts);
  const responseStatus = integerOrNull(item.response_status_code);
  const responseBytes = integerOrNull(item.response_bytes);
  const duration = integerOrNull(item.duration_ms);
  const requestHash = nullableString(item.request_body_sha256);
  const responseHash = nullableString(item.response_body_sha256);
  const errorCode = nullableString(item.error_code);
  if (
    attempts === null || attempts < 0 || attempts > 3 ||
    (responseStatus !== null && (responseStatus < 100 || responseStatus > 599)) ||
    (responseBytes !== null && (responseBytes < 0 || responseBytes > 262_144)) ||
    duration === null || duration < 0 ||
    (requestHash !== null && !/^[a-f0-9]{64}$/.test(requestHash)) ||
    (responseHash !== null && !/^[a-f0-9]{64}$/.test(responseHash)) ||
    (status === "completed" && (
      responseStatus === null || responseStatus < 200 || responseStatus > 299 ||
      responseHash === null || responseBytes === null || errorCode !== null
    )) ||
    (status !== "completed" && errorCode === null)
  ) return invalidResponse();
  return {
    id: stringField(item.id),
    connector_id: stringField(item.connector_id),
    action: enumField(item.action, ["execute", "health"] as const),
    method: enumField(item.method, ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"] as const),
    path: stringField(item.path),
    status,
    attempts,
    response_status_code: responseStatus,
    request_body_sha256: requestHash,
    response_body_sha256: responseHash,
    response_bytes: responseBytes,
    error_code: errorCode,
    started_at: stringField(item.started_at),
    completed_at: stringField(item.completed_at),
    duration_ms: duration,
  };
}

export function parseConnectorExecutionResult(value: unknown): ConnectorExecutionResult {
  const item = record(value);
  return {
    execution: parseConnectorExecution(item.execution),
    payload: jsonValue(item.payload),
  };
}

export function parseConnectorExecutionPage(value: unknown): ConnectorExecutionPage {
  const item = record(value);
  if (!Array.isArray(item.items) || item.items.length > 100) return invalidResponse();
  return { items: item.items.map(parseConnectorExecution) };
}

function parseMarketingSourceFact(value: unknown): MarketingSourceFact {
  const item = record(value);
  const sourceReference = stringField(item.source_reference);
  const fact = stringField(item.fact);
  if (
    sourceReference.length < 1 || sourceReference.length > 512 ||
    sourceReference !== sourceReference.trim() ||
    fact.length < 1 || fact.length > 2_000 || fact !== fact.trim()
  ) return invalidResponse();
  return { source_reference: sourceReference, fact };
}

function parseMarketingStage(value: unknown): MarketingStage {
  const item = record(value);
  const position = integerOrNull(item.position);
  const output = nullableString(item.output);
  const outputHash = nullableString(item.output_sha256);
  const modelId = nullableString(item.model_id);
  const executionId = nullableString(item.connector_execution_id);
  const errorCode = nullableString(item.error_code);
  const duration = integerOrNull(item.duration_ms);
  if (
    position === null || position < 1 || position > 8 ||
    (output !== null && output.length > 32_768) ||
    (outputHash !== null && !/^[a-f0-9]{64}$/.test(outputHash)) ||
    (modelId !== null && (modelId.length < 1 || modelId.length > 96)) ||
    (duration !== null && duration < 0)
  ) return invalidResponse();
  return {
    id: stringField(item.id),
    position,
    kind: enumField(item.kind, marketingStageKinds),
    status: enumField(item.status, marketingStageStatuses),
    output,
    output_sha256: outputHash,
    model_id: modelId,
    connector_execution_id: executionId,
    error_code: errorCode,
    started_at: nullableString(item.started_at),
    completed_at: nullableString(item.completed_at),
    duration_ms: duration,
  };
}

export function parseMarketingCampaign(value: unknown): MarketingCampaign {
  const item = record(value);
  if (
    !Array.isArray(item.channels) || item.channels.length < 1 || item.channels.length > 4 ||
    !Array.isArray(item.source_facts) || item.source_facts.length < 1 || item.source_facts.length > 16 ||
    !Array.isArray(item.stages) || item.stages.length !== marketingStageKinds.length
  ) return invalidResponse();
  const channels = item.channels.map((channel) => enumField(channel, marketingChannels));
  const sources = item.source_facts.map(parseMarketingSourceFact);
  const stages = item.stages.map(parseMarketingStage);
  const currentStage = nullableString(item.current_stage);
  if (
    new Set(channels).size !== channels.length ||
    new Set(sources.map((source) => `${source.source_reference}\0${source.fact}`)).size !== sources.length ||
    stages.some((stage, index) => stage.position !== index + 1 || stage.kind !== marketingStageKinds[index]) ||
    (currentStage !== null && !marketingStageKinds.includes(currentStage as MarketingStageKind))
  ) return invalidResponse();
  return {
    id: stringField(item.id),
    name: stringField(item.name),
    objective: stringField(item.objective),
    product: stringField(item.product),
    audience: stringField(item.audience),
    channels,
    source_facts: sources,
    publisher_connector_id: nullableString(item.publisher_connector_id),
    publish_path: nullableString(item.publish_path),
    status: enumField(item.status, marketingCampaignStatuses),
    current_stage: currentStage as MarketingStageKind | null,
    analytics: item.analytics === null ? null : jsonValue(item.analytics),
    error_code: nullableString(item.error_code),
    created_at: stringField(item.created_at),
    updated_at: stringField(item.updated_at),
    started_at: nullableString(item.started_at),
    approved_at: nullableString(item.approved_at),
    published_at: nullableString(item.published_at),
    completed_at: nullableString(item.completed_at),
    stages,
  };
}

export function parseMarketingCampaignPage(value: unknown): MarketingCampaignPage {
  const item = record(value);
  if (!Array.isArray(item.items) || item.items.length > 50) return invalidResponse();
  return { items: item.items.map(parseMarketingCampaign) };
}

function boundedInteger(value: unknown, minimum: number, maximum: number): number {
  const parsed = integerOrNull(value);
  if (parsed === null || parsed < minimum || parsed > maximum) return invalidResponse();
  return parsed;
}

function parseMarketWatchItem(value: unknown): MarketWatchItem {
  const item = record(value);
  const symbol = stringField(item.symbol);
  const displayName = stringField(item.display_name);
  if (!/^[A-Z0-9][A-Z0-9._:/-]{0,23}$/.test(symbol) || displayName.length < 1 || displayName.length > 120) return invalidResponse();
  return {
    id: stringField(item.id),
    asset_class: enumField(item.asset_class, marketAssetClasses),
    symbol,
    display_name: displayName,
    created_at: stringField(item.created_at),
  };
}

function parsePaperPosition(value: unknown): PaperPosition {
  const item = record(value);
  const symbol = stringField(item.symbol);
  if (!/^[A-Z0-9][A-Z0-9._:/-]{0,23}$/.test(symbol)) return invalidResponse();
  return {
    id: stringField(item.id),
    asset_class: enumField(item.asset_class, marketAssetClasses),
    symbol,
    quantity_micros: boundedInteger(item.quantity_micros, 1, 1_000_000_000_000_000),
    cost_basis_minor: boundedInteger(item.cost_basis_minor, 0, 1_000_000_000_000_000),
    updated_at: stringField(item.updated_at),
  };
}

export function parsePaperOrder(value: unknown): PaperOrder {
  const item = record(value);
  const symbol = stringField(item.symbol);
  const rejectionCode = nullableString(item.rejection_code);
  const status = enumField(item.status, paperOrderStatuses);
  if (
    item.execution_mode !== "paper" || !/^[A-Z0-9][A-Z0-9._:/-]{0,23}$/.test(symbol) ||
    (status === "executed" && rejectionCode !== null) ||
    (status === "rejected" && rejectionCode === null)
  ) return invalidResponse();
  return {
    id: stringField(item.id),
    asset_class: enumField(item.asset_class, marketAssetClasses),
    symbol,
    side: enumField(item.side, paperOrderSides),
    quantity_micros: boundedInteger(item.quantity_micros, 1, 1_000_000_000_000_000),
    price_minor: boundedInteger(item.price_minor, 1, 1_000_000_000_000_000),
    notional_minor: boundedInteger(item.notional_minor, 1, 1_000_000_000_000_000),
    source_reference: stringField(item.source_reference),
    observed_at: stringField(item.observed_at),
    status,
    rejection_code: rejectionCode,
    cash_after_minor: boundedInteger(item.cash_after_minor, 0, 1_000_000_000_000_000),
    created_at: stringField(item.created_at),
    execution_mode: "paper",
  };
}

export function parseMarketAlert(value: unknown): MarketAlert {
  const item = record(value);
  const symbol = stringField(item.symbol);
  const lastPrice = item.last_price_minor === null
    ? null
    : boundedInteger(item.last_price_minor, 1, 1_000_000_000_000_000);
  if (!/^[A-Z0-9][A-Z0-9._:/-]{0,23}$/.test(symbol)) return invalidResponse();
  return {
    id: stringField(item.id),
    asset_class: enumField(item.asset_class, marketAssetClasses),
    symbol,
    condition: enumField(item.condition, marketAlertConditions),
    threshold_minor: boundedInteger(item.threshold_minor, 1, 1_000_000_000_000_000),
    status: enumField(item.status, marketAlertStatuses),
    last_price_minor: lastPrice,
    last_source_reference: nullableString(item.last_source_reference),
    last_observed_at: nullableString(item.last_observed_at),
    created_at: stringField(item.created_at),
    triggered_at: nullableString(item.triggered_at),
  };
}

export function parseFinanceArtifact(value: unknown): FinanceArtifact {
  const item = record(value);
  const inputHash = stringField(item.input_sha256);
  const outputHash = stringField(item.output_sha256);
  const output = stringField(item.output);
  if (
    !/^[a-f0-9]{64}$/.test(inputHash) || !/^[a-f0-9]{64}$/.test(outputHash) ||
    output.length < 1 || output.length > 65_536
  ) return invalidResponse();
  return {
    id: stringField(item.id),
    kind: enumField(item.kind, financeArtifactKinds),
    title: stringField(item.title),
    source_reference: stringField(item.source_reference),
    input_sha256: inputHash,
    output,
    output_sha256: outputHash,
    model_id: stringField(item.model_id),
    duration_ms: boundedInteger(item.duration_ms, 0, 2_147_483_647),
    created_at: stringField(item.created_at),
  };
}

export function parseFinanceWorkspace(value: unknown): FinanceWorkspace {
  const item = record(value);
  if (
    item.execution_mode !== "paper" || item.live_broker_status !== "external_dependency" ||
    !Array.isArray(item.watch_items) || item.watch_items.length > 100 ||
    !Array.isArray(item.positions) || item.positions.length > 100 ||
    !Array.isArray(item.orders) || item.orders.length > 1_000 ||
    !Array.isArray(item.alerts) || item.alerts.length > 100 ||
    !Array.isArray(item.artifacts) || item.artifacts.length > 250
  ) return invalidResponse();
  const baseCurrency = stringField(item.base_currency);
  const maxOrder = boundedInteger(item.max_order_bps, 1, 10_000);
  const maxPosition = boundedInteger(item.max_position_bps, 1, 10_000);
  if (!/^[A-Z]{3}$/.test(baseCurrency) || maxOrder > maxPosition) return invalidResponse();
  return {
    id: stringField(item.id),
    name: stringField(item.name),
    base_currency: baseCurrency,
    initial_cash_minor: boundedInteger(item.initial_cash_minor, 1, 1_000_000_000_000_000),
    cash_minor: boundedInteger(item.cash_minor, 0, 1_000_000_000_000_000),
    max_order_bps: maxOrder,
    max_position_bps: maxPosition,
    created_at: stringField(item.created_at),
    updated_at: stringField(item.updated_at),
    watch_items: item.watch_items.map(parseMarketWatchItem),
    positions: item.positions.map(parsePaperPosition),
    orders: item.orders.map(parsePaperOrder),
    alerts: item.alerts.map(parseMarketAlert),
    artifacts: item.artifacts.map(parseFinanceArtifact),
    execution_mode: "paper",
    live_broker_status: "external_dependency",
  };
}

export function parseFinanceWorkspacePage(value: unknown): FinanceWorkspacePage {
  const item = record(value);
  if (!Array.isArray(item.items) || item.items.length > 10) return invalidResponse();
  return { items: item.items.map(parseFinanceWorkspace) };
}

export function parsePortfolioAnalysis(value: unknown): PortfolioAnalysis {
  const item = record(value);
  return { portfolio: parseFinanceArtifact(item.portfolio), risk: parseFinanceArtifact(item.risk) };
}

export function parseMarketAlertEvaluation(value: unknown): MarketAlertEvaluation {
  const item = record(value);
  if (!Array.isArray(item.items) || item.items.length > 100) return invalidResponse();
  return { items: item.items.map(parseMarketAlert) };
}

export function parseLearningAttempt(value: unknown): LearningAttempt {
  const item = record(value);
  return {
    id: stringField(item.id),
    activity_id: stringField(item.activity_id),
    is_correct: booleanField(item.is_correct),
    score_bps: boundedInteger(item.score_bps, 0, 10_000),
    feedback: stringField(item.feedback),
    created_at: stringField(item.created_at),
  };
}

function parseLearningActivity(value: unknown): LearningActivity {
  const item = record(value);
  if (
    item.explanation_available_after_attempt !== true ||
    !Array.isArray(item.attempts) || item.attempts.length > 10
  ) return invalidResponse();
  return {
    id: stringField(item.id),
    lesson_id: stringField(item.lesson_id),
    kind: enumField(item.kind, learningActivityKinds),
    prompt: stringField(item.prompt),
    explanation_available_after_attempt: true,
    difficulty: boundedInteger(item.difficulty, 1, 5),
    max_attempts: boundedInteger(item.max_attempts, 1, 10),
    attempts: item.attempts.map(parseLearningAttempt),
    created_at: stringField(item.created_at),
  };
}

function parseLearningLesson(value: unknown): LearningLesson {
  const item = record(value);
  if (
    !Array.isArray(item.objectives) || item.objectives.length < 1 || item.objectives.length > 16 ||
    !Array.isArray(item.activities) || item.activities.length > 30
  ) return invalidResponse();
  const content = nullableString(item.content);
  const outputHash = nullableString(item.output_sha256);
  const modelId = nullableString(item.model_id);
  const status = enumField(item.status, learningLessonStatuses);
  if (
    (content !== null && (content.length < 1 || content.length > 65_536)) ||
    (outputHash !== null && !/^[a-f0-9]{64}$/.test(outputHash)) ||
    (status === "planned" && (content !== null || outputHash !== null || modelId !== null)) ||
    (status !== "planned" && (content === null || outputHash === null || modelId === null))
  ) return invalidResponse();
  return {
    id: stringField(item.id),
    position: boundedInteger(item.position, 1, 50),
    title: stringField(item.title),
    objectives: item.objectives.map(stringField),
    difficulty: boundedInteger(item.difficulty, 1, 5),
    status,
    content,
    output_sha256: outputHash,
    model_id: modelId,
    memory_context_count: boundedInteger(item.memory_context_count, 0, 4),
    score_bps: item.score_bps === null ? null : boundedInteger(item.score_bps, 0, 10_000),
    activities: item.activities.map(parseLearningActivity),
    created_at: stringField(item.created_at),
    generated_at: nullableString(item.generated_at),
    completed_at: nullableString(item.completed_at),
  };
}

export function parseLearningReviewItem(value: unknown): LearningReviewItem {
  const item = record(value);
  return {
    id: stringField(item.id),
    front: stringField(item.front),
    back: stringField(item.back),
    interval_days: boundedInteger(item.interval_days, 0, 36_500),
    ease_milli: boundedInteger(item.ease_milli, 1_300, 3_000),
    repetitions: boundedInteger(item.repetitions, 0, 10_000),
    due_at: stringField(item.due_at),
    last_quality: item.last_quality === null ? null : boundedInteger(item.last_quality, 0, 5),
    created_at: stringField(item.created_at),
    updated_at: stringField(item.updated_at),
  };
}

export function parseLearningProgram(value: unknown): LearningProgram {
  const item = record(value);
  if (
    !Array.isArray(item.lessons) || item.lessons.length < 1 || item.lessons.length > 50 ||
    !Array.isArray(item.review_items) || item.review_items.length > 500
  ) return invalidResponse();
  const totalLessons = boundedInteger(item.total_lessons, 1, 50);
  const completedLessons = boundedInteger(item.completed_lessons, 0, totalLessons);
  const totalAttempts = boundedInteger(item.total_attempts, 0, 1_000_000);
  const correctAttempts = boundedInteger(item.correct_attempts, 0, totalAttempts);
  const start = boundedInteger(item.start_difficulty, 1, 5);
  const current = boundedInteger(item.current_difficulty, 1, 5);
  const target = boundedInteger(item.target_difficulty, 1, 5);
  const progress = boundedInteger(item.progress_bps, 0, 10_000);
  const accuracy = item.accuracy_bps === null ? null : boundedInteger(item.accuracy_bps, 0, 10_000);
  if (
    start > target || current < start || current > target ||
    progress !== Math.floor(completedLessons * 10_000 / totalLessons) ||
    accuracy !== (totalAttempts === 0 ? null : Math.floor(correctAttempts * 10_000 / totalAttempts))
  ) return invalidResponse();
  const lessons = item.lessons.map(parseLearningLesson);
  if (lessons.some((lesson, index) => lesson.position !== index + 1)) return invalidResponse();
  return {
    id: stringField(item.id),
    subject: stringField(item.subject),
    goal: stringField(item.goal),
    target_language: stringField(item.target_language),
    instruction_language: stringField(item.instruction_language),
    start_difficulty: start,
    current_difficulty: current,
    target_difficulty: target,
    weekly_minutes: boundedInteger(item.weekly_minutes, 15, 10_080),
    adaptive_difficulty: booleanField(item.adaptive_difficulty),
    status: enumField(item.status, learningProgramStatuses),
    total_lessons: totalLessons,
    completed_lessons: completedLessons,
    total_attempts: totalAttempts,
    correct_attempts: correctAttempts,
    progress_bps: progress,
    accuracy_bps: accuracy,
    lessons,
    review_items: item.review_items.map(parseLearningReviewItem),
    created_at: stringField(item.created_at),
    updated_at: stringField(item.updated_at),
    completed_at: nullableString(item.completed_at),
  };
}

export function parseLearningProgramPage(value: unknown): LearningProgramPage {
  const item = record(value);
  if (!Array.isArray(item.items) || item.items.length > 20) return invalidResponse();
  return { items: item.items.map(parseLearningProgram) };
}

export function parseLearningCapabilities(value: unknown): LearningCapabilities {
  const item = record(value);
  if (
    item.teacher_mode !== true || item.speaking_partner !== true ||
    item.exam_mode !== true || item.vocabulary_trainer !== true ||
    item.spaced_repetition !== true || item.pronunciation_scoring !== false ||
    item.pronunciation_status !== "external_dependency" ||
    !Array.isArray(item.pronunciation_dependencies) ||
    item.pronunciation_dependencies.length !== 1 ||
    item.pronunciation_dependencies[0] !== "pronunciation_scoring_provider"
  ) return invalidResponse();
  return {
    teacher_mode: true,
    speaking_partner: true,
    exam_mode: true,
    vocabulary_trainer: true,
    spaced_repetition: true,
    pronunciation_scoring: false,
    pronunciation_status: "external_dependency",
    pronunciation_dependencies: ["pronunciation_scoring_provider"],
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
    activeRuns === null || activeRuns < 0 || activeRuns > 100 ||
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
  if (
    !Array.isArray(item.attempts) || item.attempts.length > 48 ||
    !Array.isArray(item.plan) || item.plan.length > 16 ||
    !Array.isArray(item.events) || item.events.length > 128
  ) return invalidResponse();
  const plan = item.plan.map((rawStep) => {
    const step = record(rawStep);
    if (!Array.isArray(step.permissions) || step.permissions.length > agentPermissions.length) return invalidResponse();
    const permissions = step.permissions.map((permission) => enumField(permission, agentPermissions));
    if (new Set(permissions).size !== permissions.length) return invalidResponse();
    return {
      step_id: stringField(step.step_id),
      agent: enumField(step.agent, agentKinds),
      task: enumField(step.task, modelTasks),
      permissions,
      requires_objective_evidence: booleanField(step.requires_objective_evidence),
    };
  });
  const events = item.events.map((rawEvent) => {
    const event = record(rawEvent);
    const sequence = integerOrNull(event.sequence);
    const attempt = event.attempt === null ? null : integerOrNull(event.attempt);
    const modelId = nullableString(event.model_id);
    if (
      sequence === null || sequence < 1 || sequence > 10_000 ||
      (attempt !== null && (attempt < 1 || attempt > 3)) ||
      (modelId !== null && !/^[a-z0-9][a-z0-9_-]{0,63}:[a-f0-9]{24}$/.test(modelId))
    ) return invalidResponse();
    return {
      sequence,
      status: enumField(event.status, agentRuntimeStatuses),
      created_at: stringField(event.created_at),
      step_id: nullableString(event.step_id),
      attempt,
      agent: event.agent === null ? null : enumField(event.agent, agentKinds),
      model_id: modelId,
    };
  });
  if (events.some((event, index) => index > 0 && event.sequence <= events[index - 1]!.sequence)) {
    return invalidResponse();
  }
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
    goal: stringField(item.goal),
    source: enumField(item.source, agentInputSources),
    task: enumField(item.task, modelTasks),
    specialist: item.specialist === null ? null : enumField(item.specialist, agentKinds),
    status: enumField(item.status, agentRuntimeStatuses),
    created_at: stringField(item.created_at),
    updated_at: stringField(item.updated_at),
    output: nullableString(item.output),
    failure_code: nullableString(item.failure_code),
    plan,
    events,
    attempts,
  };
}

export function parseAgentRunEvent(value: unknown): AgentRunEvent {
  const parsed = parseAgentRun({
    id: "event-parser",
    goal: "event",
    source: "text",
    task: "general_chat",
    specialist: null,
    status: "queued",
    created_at: "event",
    updated_at: "event",
    output: null,
    failure_code: null,
    plan: [],
    events: [value],
    attempts: [],
  });
  return parsed.events[0]!;
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
