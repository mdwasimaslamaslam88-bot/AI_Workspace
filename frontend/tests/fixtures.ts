import type {
  ConversationSummary,
  CurrentUser,
  LocalModel,
  Message,
  ProductCapability,
  SystemDiagnostics,
} from "../src/api/contracts";

export const token = "frontend-test-bearer-token";
export const rawSecret = "raw-secret-that-must-never-render";

export const user: CurrentUser = {
  id: "11111111-1111-4111-8111-111111111111",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

export const model: LocalModel = {
  model_id: "ollama-local:aaaaaaaaaaaaaaaaaaaaaaaa",
  display_name: "Local Model",
  runtime_id: "ollama-local",
  modality: "text",
  family: "Local",
  parameter_class: "8B",
  capabilities: ["chat", "text_generation"],
  context_window: 8192,
  quantization: "Q4",
  estimated_vram_bytes: null,
  availability: "available",
  scale_class: "7b_8b",
  required_vram_bytes: 7_000_000_000,
  required_ram_bytes: 8_000_000_000,
  installed: true,
  runnable_now: true,
  hardware_class: "gpu_8_to_15gb",
  fallback_model_id: null,
};

export const visionModel: LocalModel = {
  ...model,
  model_id: "ollama-local:dddddddddddddddddddddddd",
  display_name: "Local Vision Model",
  capabilities: ["chat", "text_generation", "vision_input"],
};

export const productCapabilities: ProductCapability[] = [
  "chat",
  "vision_input",
  "attachments",
  "documents_rag",
  "personal_memory",
  "bounded_tools",
  "bounded_workflows",
].map((id) => ({
  id: id as ProductCapability["id"],
  status: "available",
  blocking_reasons: [],
}));
productCapabilities.push(
  {
    id: "image_generation",
    status: "unavailable",
    blocking_reasons: ["local_image_runtime_and_model_required"],
  },
  {
    id: "image_editing",
    status: "unavailable",
    blocking_reasons: ["local_image_edit_runtime_and_model_required"],
  },
  {
    id: "voice_input",
    status: "unavailable",
    blocking_reasons: ["local_voice_runtime_and_models_required"],
  },
  {
    id: "voice_output",
    status: "unavailable",
    blocking_reasons: ["local_voice_runtime_and_models_required"],
  },
);

export const systemDiagnostics: SystemDiagnostics = {
  mode: "local",
  services: [
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
  ].map((id) => ({
    id: id as SystemDiagnostics["services"][number]["id"],
    status: id === "remote_gateway" ? "unconfigured" : "ready",
  })),
  gpus: [
    {
      model: "Test GPU",
      vram_bytes: 12 * 1024 ** 3,
      hardware_class: "gpu_8_to_15gb",
      status: "ready",
    },
  ],
};

export const conversation: ConversationSummary = {
  id: "22222222-2222-4222-8222-222222222222",
  title: "Local chat",
  created_at: "2026-01-02T00:00:00Z",
  updated_at: "2026-01-02T00:00:00Z",
};

export function message(
  sequenceNumber: number,
  role: Message["role"],
  content: string,
): Message {
  return {
    id: `33333333-3333-4333-8333-${String(sequenceNumber).padStart(12, "0")}`,
    conversation_id: conversation.id,
    role,
    content,
    sequence_number: sequenceNumber,
    created_at: "2026-01-02T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
    attachments: [],
    citations: [],
  };
}

export function jsonResponse(
  body: unknown,
  status = 200,
  requestId = "request-test-id",
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
      "X-Request-ID": requestId,
    },
  });
}

export function errorEnvelope(rawMessage = rawSecret) {
  return {
    success: false,
    error: {
      code: "HTTP_ERROR",
      message: rawMessage,
      details: [{ internal_runtime_url: "http://127.0.0.1:11434" }],
    },
    path: "/api/v1/test",
    timestamp: "2026-01-01T00:00:00Z",
  };
}
