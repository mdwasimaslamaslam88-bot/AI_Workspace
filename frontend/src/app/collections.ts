import type {
  ConversationSummary,
  LocalModel,
  Message,
} from "../api/contracts";

export function selectableTextModels(models: LocalModel[]): LocalModel[] {
  return models.filter(
    (model) =>
      model.modality === "text" &&
      model.availability === "available" &&
      model.capabilities.includes("text_generation"),
  );
}

export function modelSupportsVision(model: LocalModel | null): boolean {
  return model?.capabilities.includes("vision_input") ?? false;
}

export function isVisionImageMediaType(
  mediaType: string | null | undefined,
): boolean {
  return mediaType === "image/png" || mediaType === "image/jpeg";
}

export function isDocumentMediaType(
  mediaType: string | null | undefined,
): boolean {
  return (
    mediaType === "text/plain" ||
    mediaType === "text/csv" ||
    mediaType === "application/pdf" ||
    mediaType ===
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
  );
}

export function mergeConversations(
  existing: ConversationSummary[],
  incoming: ConversationSummary[],
): ConversationSummary[] {
  const byId = new Map(existing.map((item) => [item.id, item]));
  for (const item of incoming) byId.set(item.id, item);
  return [...byId.values()];
}

export function mergeMessages(
  existing: Message[],
  incoming: Message[],
): Message[] {
  const byId = new Map(existing.map((message) => [message.id, message]));
  for (const message of incoming) byId.set(message.id, message);
  return [...byId.values()].sort(
    (left, right) => left.sequence_number - right.sequence_number,
  );
}
