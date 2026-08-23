import type { MessageCitation } from "@work-station/shared";

export function citationLocation(citation: MessageCitation): string {
  const parts: string[] = [];
  if (citation.page_number !== null) parts.push(`page ${citation.page_number}`);
  if (citation.row_start !== null) {
    parts.push(
      citation.row_end === null || citation.row_end === citation.row_start
        ? `row ${citation.row_start}`
        : `rows ${citation.row_start}–${citation.row_end}`,
    );
  }
  if (citation.section !== null) parts.push(citation.section);
  return parts.join(" · ");
}

export function citationSourceLabel(citation: MessageCitation): string {
  if (citation.state === "deleted") return "Deleted private source";
  return citation.original_filename ?? "Private source";
}
