import type {
  ConversationSummary,
  CurrentUser,
  LocalModel,
  Message,
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
};

export const visionModel: LocalModel = {
  ...model,
  model_id: "ollama-local:dddddddddddddddddddddddd",
  display_name: "Local Vision Model",
  capabilities: ["chat", "text_generation", "vision_input"],
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
