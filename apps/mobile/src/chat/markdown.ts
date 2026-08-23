export type MobileMarkdownBlock =
  | { kind: "heading"; level: 1 | 2 | 3; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "quote"; text: string }
  | { kind: "list_item"; marker: string; text: string }
  | { kind: "code"; language: string | null; text: string }
  | { kind: "separator" };

export type MobileInlineSpan = {
  kind: "text" | "strong" | "emphasis" | "code";
  text: string;
};

const MAX_LANGUAGE_LENGTH = 32;

function appendParagraph(blocks: MobileMarkdownBlock[], lines: string[]) {
  if (lines.length === 0) return;
  blocks.push({ kind: "paragraph", text: lines.join("\n") });
  lines.length = 0;
}

/**
 * Parses the presentation-only Markdown subset used by the native client.
 * HTML and links are intentionally left as inert text: model output must never
 * trigger navigation, a WebView, or an unapproved network request.
 */
export function parseMobileMarkdown(content: string): MobileMarkdownBlock[] {
  const blocks: MobileMarkdownBlock[] = [];
  const paragraph: string[] = [];
  const lines = content.replace(/\r\n?/g, "\n").split("\n");
  let code: string[] | null = null;
  let language: string | null = null;

  for (const line of lines) {
    if (code !== null) {
      if (/^\s*```\s*$/.test(line)) {
        blocks.push({ kind: "code", language, text: code.join("\n") });
        code = null;
        language = null;
      } else {
        code.push(line);
      }
      continue;
    }

    const fence = /^\s*```([^`]*)$/.exec(line);
    if (fence !== null) {
      appendParagraph(blocks, paragraph);
      const candidate = (fence[1] ?? "").trim();
      language = candidate.length > 0
        ? candidate.slice(0, MAX_LANGUAGE_LENGTH)
        : null;
      code = [];
      continue;
    }

    if (line.trim().length === 0) {
      appendParagraph(blocks, paragraph);
      continue;
    }

    const heading = /^(#{1,3})\s+(.+)$/.exec(line);
    if (heading !== null) {
      appendParagraph(blocks, paragraph);
      blocks.push({
        kind: "heading",
        level: heading[1]!.length as 1 | 2 | 3,
        text: heading[2]!,
      });
      continue;
    }

    if (/^\s*(?:---+|___+|\*\*\*+)\s*$/.test(line)) {
      appendParagraph(blocks, paragraph);
      blocks.push({ kind: "separator" });
      continue;
    }

    const quote = /^\s*>\s?(.*)$/.exec(line);
    if (quote !== null) {
      appendParagraph(blocks, paragraph);
      blocks.push({ kind: "quote", text: quote[1] ?? "" });
      continue;
    }

    const unordered = /^\s*[-+*]\s+(.+)$/.exec(line);
    if (unordered !== null) {
      appendParagraph(blocks, paragraph);
      blocks.push({ kind: "list_item", marker: "•", text: unordered[1]! });
      continue;
    }

    const ordered = /^\s*(\d{1,4})[.)]\s+(.+)$/.exec(line);
    if (ordered !== null) {
      appendParagraph(blocks, paragraph);
      blocks.push({
        kind: "list_item",
        marker: `${ordered[1]}.`,
        text: ordered[2]!,
      });
      continue;
    }

    paragraph.push(line);
  }

  appendParagraph(blocks, paragraph);
  if (code !== null) {
    blocks.push({ kind: "code", language, text: code.join("\n") });
  }
  return blocks;
}

export function parseMobileInlineMarkdown(text: string): MobileInlineSpan[] {
  const spans: MobileInlineSpan[] = [];
  const pattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*|__[^_\n]+__|\*[^*\n]+\*|_[^_\n]+_)/g;
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    const index = match.index ?? 0;
    if (index > cursor) spans.push({ kind: "text", text: text.slice(cursor, index) });
    const token = match[0];
    if (token.startsWith("`")) {
      spans.push({ kind: "code", text: token.slice(1, -1) });
    } else if (token.startsWith("**") || token.startsWith("__")) {
      spans.push({ kind: "strong", text: token.slice(2, -2) });
    } else {
      spans.push({ kind: "emphasis", text: token.slice(1, -1) });
    }
    cursor = index + token.length;
  }
  if (cursor < text.length) spans.push({ kind: "text", text: text.slice(cursor) });
  return spans.length > 0 ? spans : [{ kind: "text", text }];
}
