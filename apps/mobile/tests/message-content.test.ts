import { beforeEach, describe, expect, it, vi } from "vitest";

import * as Clipboard from "expo-clipboard";
import type { MessageAttachment } from "@work-station/shared";

import {
  privateAttachmentDetails,
  privateAttachmentLabel,
} from "../src/chat/attachments";
import { copyPrivateMessageContent } from "../src/chat/clipboard";
import {
  parseMobileInlineMarkdown,
  parseMobileMarkdown,
} from "../src/chat/markdown";

vi.mock("expo-clipboard", () => ({ setStringAsync: vi.fn(async () => true) }));

describe("mobile private message presentation", () => {
  beforeEach(() => vi.mocked(Clipboard.setStringAsync).mockClear());

  it("renders a bounded native Markdown subset without actionable links or HTML", () => {
    const blocks = parseMobileMarkdown([
      "# Result",
      "",
      "**Important** and `literal` [label](https://example.invalid/private)",
      "",
      "- first",
      "> quoted",
      "```ts",
      "const value = '<script>never executes</script>';",
      "```",
    ].join("\n"));

    expect(blocks).toEqual([
      { kind: "heading", level: 1, text: "Result" },
      {
        kind: "paragraph",
        text: "**Important** and `literal` [label](https://example.invalid/private)",
      },
      { kind: "list_item", marker: "•", text: "first" },
      { kind: "quote", text: "quoted" },
      {
        kind: "code",
        language: "ts",
        text: "const value = '<script>never executes</script>';",
      },
    ]);
    expect(parseMobileInlineMarkdown("**safe** and `code`")).toEqual([
      { kind: "strong", text: "safe" },
      { kind: "text", text: " and " },
      { kind: "code", text: "code" },
    ]);
  });

  it("copies only after an explicit owner action", async () => {
    expect(Clipboard.setStringAsync).not.toHaveBeenCalled();
    await copyPrivateMessageContent("private assistant output");
    expect(Clipboard.setStringAsync).toHaveBeenCalledWith("private assistant output");
  });

  it("describes active attachments without exposing storage paths", () => {
    const attachment: MessageAttachment = {
      id: "11111111-1111-4111-8111-111111111111",
      position: 0,
      state: "active",
      original_filename: "answer.png",
      media_type: "image/png",
      byte_size: 512,
      provenance_kind: "image_generation",
      source_asset_id: null,
    };
    expect(privateAttachmentLabel(attachment)).toBe("answer.png");
    expect(privateAttachmentDetails(attachment)).toBe("image/png · 512 bytes");
    expect(privateAttachmentDetails({ ...attachment, state: "deleted" })).toBeNull();
    expect(privateAttachmentLabel({ ...attachment, state: "deleted" })).toBe(
      "Deleted attachment",
    );
  });
});
