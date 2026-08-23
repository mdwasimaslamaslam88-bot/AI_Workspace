import type { MessageAttachment } from "@work-station/shared";

export type PrivateAttachmentKind = "audio" | "document" | "image";

export function privateAttachmentKind(attachment: MessageAttachment): PrivateAttachmentKind {
  const mediaType = attachment.media_type ?? "";
  if (mediaType.startsWith("audio/")) return "audio";
  if (mediaType.startsWith("image/")) return "image";
  return "document";
}

export function privateAttachmentLabel(attachment: MessageAttachment): string {
  if (attachment.state === "deleted") return "Deleted attachment";
  return attachment.original_filename ?? "Private attachment";
}

export function privateAttachmentDetails(attachment: MessageAttachment): string | null {
  if (attachment.state === "deleted") return null;
  const parts = [attachment.media_type];
  if (attachment.byte_size !== null) parts.push(`${attachment.byte_size} bytes`);
  return parts.filter((part): part is string => part !== null).join(" · ") || null;
}
