import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { IndexedDocument } from "../src/api/contracts";
import { mergeMessages } from "../src/app/collections";
import { ChatView, type SafeNotice } from "../src/features/chat/ChatView";
import { conversation, message, rawSecret } from "./fixtures";

const indexedDocument: IndexedDocument = {
  id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
  asset_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  status: "ready",
  source_state: "active",
  original_filename: "notes.txt",
  media_type: "text/plain",
  chunk_count: 1,
  character_count: 5,
  failure_code: null,
  created_at: "2026-01-03T00:00:00Z",
  updated_at: "2026-01-03T00:00:01Z",
  completed_at: "2026-01-03T00:00:01Z",
};

const baseProps = {
  conversation,
  creatingNew: false,
  canGenerate: true,
  messages: [message(1, "user", "hello")],
  nextCursor: null,
  loadingMessages: false,
  loadingMoreMessages: false,
  creatingConversation: false,
  generating: false,
  notice: null,
  onCreateConversation: vi.fn(async () => undefined),
  onCancelNew: vi.fn(),
  onGenerate: vi.fn(async () => undefined),
  onEditAndResend: vi.fn(async () => undefined),
  onRegenerate: vi.fn(async () => undefined),
  onCancelGeneration: vi.fn(),
  onLoadMoreMessages: vi.fn(),
  onReloadMessages: vi.fn(),
  onUploadAttachment: vi.fn(async () => Promise.reject(new Error("unused"))),
  onIngestDocument: vi.fn(async () => indexedDocument),
  onDownloadAttachment: vi.fn(async () => new Blob()),
  onDeleteAttachment: vi.fn(async () => undefined),
};

describe("ChatView", () => {
  it("renders persisted messages as text and merges refreshed pages without duplicates", () => {
    const unsafe = message(2, "assistant", `<img src=x onerror="${rawSecret}">`);
    const merged = mergeMessages(
      [baseProps.messages[0]],
      [baseProps.messages[0], unsafe],
    );
    const { container } = render(<ChatView {...baseProps} messages={merged} />);
    expect(screen.getByText(/<img src=x/)).toBeVisible();
    expect(container.querySelector("img")).toBeNull();
    expect(merged).toHaveLength(2);
  });

  it("renders safe GFM and copies the exact message without activating model links", async () => {
    const content = [
      "## Result",
      "",
      "- first item",
      "- [reference](https://example.test/private)",
      "",
      "```ts",
      "const answer = 42;",
      "```",
    ].join("\n");
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    const { container } = render(
      <ChatView
        {...baseProps}
        messages={[message(2, "assistant", content)]}
      />,
    );

    expect(screen.getByRole("heading", { name: "Result" })).toBeVisible();
    expect(screen.getByText("first item")).toBeVisible();
    expect(screen.getByText("const answer = 42;")).toBeVisible();
    expect(container.querySelector("a")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Copy assistant message" }));
    expect(writeText).toHaveBeenCalledWith(content);
    expect(screen.getByRole("button", { name: "Copy assistant message" })).toHaveTextContent("Copied");
  });

  it("loads another bounded history page", async () => {
    const onLoadMoreMessages = vi.fn();
    render(
      <ChatView
        {...baseProps}
        nextCursor={1}
        onLoadMoreMessages={onLoadMoreMessages}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Load more messages" }));
    expect(onLoadMoreMessages).toHaveBeenCalledOnce();
  });

  it("edits and regenerates by creating immutable text branches", async () => {
    const userMessage = message(1, "user", "original prompt");
    const assistantMessage = message(2, "assistant", "original answer");
    const onEditAndResend = vi.fn(async () => undefined);
    const onRegenerate = vi.fn(async () => undefined);
    render(
      <ChatView
        {...baseProps}
        messages={[userMessage, assistantMessage]}
        onEditAndResend={onEditAndResend}
        onRegenerate={onRegenerate}
      />,
    );

    await userEvent.click(
      screen.getByRole("button", { name: "Edit and resend in a branch" }),
    );
    const editor = screen.getByLabelText(
      "Edit user message for a new immutable branch",
    );
    await userEvent.clear(editor);
    await userEvent.type(editor, "edited prompt");
    await userEvent.click(
      screen.getByRole("button", { name: "Send edited branch" }),
    );
    expect(onEditAndResend).toHaveBeenCalledWith(userMessage, "edited prompt");

    await userEvent.click(
      screen.getByRole("button", { name: "Regenerate in a branch" }),
    );
    expect(onRegenerate).toHaveBeenCalledWith(userMessage);
  });

  it("submits an exact prompt and clears the composer", async () => {
    const onGenerate = vi.fn(async () => undefined);
    render(<ChatView {...baseProps} messages={[message(2, "assistant", "ready")]} onGenerate={onGenerate} />);
    const prompt = screen.getByLabelText("Message");
    await userEvent.type(prompt, "  preserve this prompt  ");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(onGenerate).toHaveBeenCalledWith("  preserve this prompt  ");
    expect(prompt).toHaveValue("");
  });

  it("shows pending generation and exposes explicit cancellation", async () => {
    const onCancelGeneration = vi.fn();
    render(
      <ChatView
        {...baseProps}
        generating
        onCancelGeneration={onCancelGeneration}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Generating");
    expect(screen.getByLabelText("Message")).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancelGeneration).toHaveBeenCalledOnce();
    expect(screen.queryByText(/assistant response/i)).not.toBeInTheDocument();
  });

  it("runs bounded local image generation from the unified chat", async () => {
    const onGenerateImage = vi.fn(async () => undefined);
    render(
      <ChatView
        {...baseProps}
        imageGenerationAvailable
        onGenerateImage={onGenerateImage}
      />,
    );

    await userEvent.type(
      screen.getByLabelText("Image prompt"),
      "A private geometric lantern",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Generate image" }),
    );

    expect(onGenerateImage).toHaveBeenCalledWith(
      "A private geometric lantern",
    );
    expect(screen.getByLabelText("Image prompt")).toHaveValue("");
  });

  it("presents an authenticated edited-image comparison and preserves its source", async () => {
    const sourceId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    const outputId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    const edited = {
      ...message(2, "assistant", "Edited an image locally."),
      attachments: [
        {
          id: outputId,
          position: 1,
          state: "active" as const,
          original_filename: "local-image-edit.png",
          media_type: "image/png",
          byte_size: 128,
          provenance_kind: "image_editing" as const,
          source_asset_id: sourceId,
        },
      ],
    };
    const onDownloadAttachment = vi.fn(async () =>
      new Blob(["safe png"], { type: "image/png" }),
    );
    const onEditImage = vi.fn(async () => undefined);
    const createObjectUrl = vi
      .spyOn(URL, "createObjectURL")
      .mockReturnValueOnce("blob:original")
      .mockReturnValueOnce("blob:edited");
    const revokeObjectUrl = vi
      .spyOn(URL, "revokeObjectURL")
      .mockImplementation(() => undefined);
    const { unmount } = render(
      <ChatView
        {...baseProps}
        messages={[edited]}
        imageEditingAvailable
        onEditImage={onEditImage}
        onDownloadAttachment={onDownloadAttachment}
      />,
    );

    expect(
      await screen.findByRole("img", {
        name: "Original image before local edit",
      }),
    ).toBeVisible();
    expect(
      await screen.findByRole("img", { name: "Locally edited image" }),
    ).toBeVisible();
    expect(onDownloadAttachment).toHaveBeenCalledWith(
      sourceId,
      expect.any(AbortSignal),
    );
    expect(onDownloadAttachment).toHaveBeenCalledWith(
      outputId,
      expect.any(AbortSignal),
    );

    await userEvent.click(screen.getByRole("button", { name: "Edit locally" }));
    await userEvent.type(
      screen.getByLabelText("Edit instruction"),
      "Make the lantern blue",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Create edited copy" }),
    );
    expect(onEditImage).toHaveBeenCalledWith(
      outputId,
      "Make the lantern blue",
    );

    unmount();
    expect(revokeObjectUrl).toHaveBeenCalledWith("blob:original");
    expect(revokeObjectUrl).toHaveBeenCalledWith("blob:edited");
    createObjectUrl.mockRestore();
    revokeObjectUrl.mockRestore();
  });

  it("synthesizes, plays, and deletes an assistant response as owned audio", async () => {
    const assistant = message(2, "assistant", "Private answer for speech");
    const asset = {
      id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
      original_filename: "local-speech.wav",
      media_type: "audio/wav",
      byte_size: 128,
      content_sha256: "e".repeat(64),
      provenance_kind: "speech_synthesis" as const,
      source_asset_id: null,
      runtime_id: "piper",
      model_id: "piper:" + "e".repeat(24),
      created_at: "2026-08-23T00:00:00Z",
      deleted_at: null,
    };
    const createObjectUrl = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:voice");
    const revokeObjectUrl = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    const onSynthesizeVoice = vi.fn(async () => ({
      asset,
      audio: new Blob(["wave"]),
    }));
    const onDeleteAttachment = vi.fn(async () => undefined);
    render(
      <ChatView
        {...baseProps}
        messages={[assistant]}
        voiceOutputAvailable
        onSynthesizeVoice={onSynthesizeVoice}
        onDeleteAttachment={onDeleteAttachment}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Read aloud" }));
    await waitFor(() => expect(onSynthesizeVoice).toHaveBeenCalledWith(
      "Private answer for speech",
      expect.any(AbortSignal),
    ));
    expect(screen.getByText("Local synthesized audio is ready to download.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Delete audio" }));
    await waitFor(() => expect(onDeleteAttachment).toHaveBeenCalledWith(asset.id));
    expect(revokeObjectUrl).toHaveBeenCalledWith("blob:voice");
    createObjectUrl.mockRestore();
    revokeObjectUrl.mockRestore();
  });

  it("records bounded local audio, stops tracks, and places its transcript in the draft", async () => {
    const track = { stop: vi.fn() };
    const getUserMedia = vi.fn(async () => ({ getTracks: () => [track] }));
    const originalMediaDevices = Object.getOwnPropertyDescriptor(
      navigator,
      "mediaDevices",
    );
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia },
    });

    class LocalMediaRecorder {
      static isTypeSupported(value: string) {
        return value === "audio/webm;codecs=opus";
      }

      state: RecordingState = "inactive";
      mimeType = "audio/webm;codecs=opus";
      ondataavailable: ((event: BlobEvent) => void) | null = null;
      onstop: (() => void) | null = null;

      start() {
        this.state = "recording";
      }

      stop() {
        this.state = "inactive";
        this.ondataavailable?.({
          data: new Blob(["local audio"], { type: "audio/webm" }),
        } as BlobEvent);
        this.onstop?.();
      }
    }
    vi.stubGlobal("MediaRecorder", LocalMediaRecorder);
    const onTranscribeVoice = vi.fn(async (blob: Blob) => {
      expect(blob.size).toBeLessThanOrEqual(220_000);
      expect(blob.type).toBe("audio/webm;codecs=opus");
      return "A private local transcript";
    });

    try {
      render(
        <ChatView
          {...baseProps}
          voiceInputAvailable
          onTranscribeVoice={onTranscribeVoice}
        />,
      );
      await userEvent.click(screen.getByRole("button", { name: "Record voice" }));
      expect(getUserMedia).toHaveBeenCalledWith({ audio: true });
      expect(screen.getByRole("status")).toHaveTextContent("12 seconds max");
      await userEvent.click(
        screen.getByRole("button", { name: "Stop recording" }),
      );
      await waitFor(() => expect(onTranscribeVoice).toHaveBeenCalledOnce());
      await waitFor(() =>
        expect(screen.getByLabelText("Message")).toHaveValue(
          "A private local transcript",
        ),
      );
      expect(track.stop).toHaveBeenCalledOnce();
    } finally {
      vi.unstubAllGlobals();
      if (originalMediaDevices === undefined) {
        Reflect.deleteProperty(navigator, "mediaDevices");
      } else {
        Object.defineProperty(navigator, "mediaDevices", originalMediaDevices);
      }
    }
  });

  it("uploads bounded local audio and places its transcript in the draft", async () => {
    const onTranscribeVoice = vi.fn(async (audio: Blob) => {
      expect(audio.type).toBe("audio/wav");
      return "Transcript from an owned WAV upload";
    });
    render(
      <ChatView
        {...baseProps}
        voiceInputAvailable
        onTranscribeVoice={onTranscribeVoice}
      />,
    );

    const input = screen.getByLabelText("Upload audio for transcription");
    await userEvent.upload(
      input,
      new File(["safe wav bytes"], "private-note.wav", {
        type: "audio/wav",
      }),
    );

    await waitFor(() => expect(onTranscribeVoice).toHaveBeenCalledWith(
      expect.any(File),
      expect.any(AbortSignal),
    ));
    await waitFor(() =>
      expect(screen.getByLabelText("Message")).toHaveValue(
        "Transcript from an owned WAV upload",
      ),
    );
  });

  it.each([
    ["The conversation changed. Refresh and try again.", 409],
    ["The request is too large.", 413],
    ["Local generation is busy.", 429],
    ["The local model runtime is unavailable.", 503],
    ["Could not reach the local backend.", null],
    ["Request cancelled.", null],
  ])("renders a safe generation state for %s", (text, status) => {
    const notice: SafeNotice = {
      message: text,
      status,
      requestId: "safe-request-id",
    };
    render(<ChatView {...baseProps} notice={notice} />);
    expect(screen.getByRole("alert")).toHaveTextContent(text);
    expect(screen.getByRole("alert")).toHaveTextContent("safe-request-id");
  });

  it("creates a conversation with only backend-supported optional fields", async () => {
    const onCreateConversation = vi.fn(async () => undefined);
    render(
      <ChatView
        {...baseProps}
        conversation={null}
        creatingNew
        messages={[]}
        onCreateConversation={onCreateConversation}
      />,
    );
    await userEvent.type(screen.getByLabelText(/Title/), "My title");
    await userEvent.type(screen.getByLabelText(/System prompt/), "Be concise");
    await userEvent.type(screen.getByLabelText("Your first message"), "Hello");
    await userEvent.click(screen.getByRole("button", { name: "Create and generate" }));
    expect(onCreateConversation).toHaveBeenCalledWith({
      title: "My title",
      system_prompt: "Be concise",
      initial_message: "Hello",
    });
  });

  it("uploads an opaque file and sends only its asset ID with the prompt", async () => {
    const asset = {
      id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      original_filename: "notes.txt",
      media_type: "text/plain",
      byte_size: 5,
      content_sha256: "a".repeat(64),
      provenance_kind: "upload" as const,
      source_asset_id: null,
      runtime_id: null,
      model_id: null,
      created_at: "2026-01-03T00:00:00Z",
      deleted_at: null,
    };
    const onUploadAttachment = vi.fn(
      async (file: File, idempotencyKey: string) => {
        expect(file.name).toBe("notes.txt");
        expect(idempotencyKey).toMatch(/^[0-9a-f-]{36}$/);
        return asset;
      },
    );
    const onGenerate = vi.fn(async () => undefined);
    render(
      <ChatView
        {...baseProps}
        messages={[message(2, "assistant", "ready")]}
        onUploadAttachment={onUploadAttachment}
        onGenerate={onGenerate}
      />,
    );
    const file = new File(["notes"], "notes.txt", { type: "text/plain" });
    await userEvent.upload(screen.getByLabelText("Attach files"), file);
    await waitFor(() => expect(onUploadAttachment).toHaveBeenCalledOnce());
    expect(await screen.findByText("Document ready")).toBeVisible();
    expect(onUploadAttachment.mock.calls[0]?.[1]).toMatch(
      /^[0-9a-f-]{36}$/,
    );
    await userEvent.type(screen.getByLabelText("Message"), "Use the notes");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(onGenerate).toHaveBeenCalledWith("Use the notes", [asset.id]);
  });

  it("renders document citations as text with provenance and safe tombstones", () => {
    const cited = {
      ...message(2, "assistant", "Grounded response"),
      citations: [
        {
          asset_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          position: 1,
          state: "active" as const,
          original_filename: `<img src=x onerror="${rawSecret}">.txt`,
          page_number: 3,
          row_start: null,
          row_end: null,
          section: "Overview",
          excerpt: "A bounded source excerpt.",
        },
        {
          asset_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          position: 2,
          state: "deleted" as const,
          original_filename: null,
          page_number: null,
          row_start: null,
          row_end: null,
          section: null,
          excerpt: null,
        },
      ],
    };
    const { container } = render(<ChatView {...baseProps} messages={[cited]} />);
    expect(screen.getByRole("region", { name: "Sources" })).toHaveTextContent(
      "page 3 · Overview",
    );
    expect(screen.getByText("A bounded source excerpt.")).toBeVisible();
    expect(screen.getByText("Deleted document source")).toBeVisible();
    expect(container.querySelector("img")).toBeNull();
  });

  it("renders filenames as text, tombstones deleted assets, and revokes download URLs", async () => {
    const active = {
      ...message(2, "assistant", "file"),
      attachments: [
        {
          id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          position: 1,
          state: "active" as const,
          original_filename: `<img src=x onerror="${rawSecret}">`,
          media_type: "application/octet-stream",
          byte_size: 5,
          provenance_kind: "upload" as const,
          source_asset_id: null,
        },
      ],
    };
    const deleted = {
      ...message(3, "user", "gone"),
      attachments: [
        {
          id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          position: 1,
          state: "deleted" as const,
          original_filename: null,
          media_type: null,
          byte_size: null,
          provenance_kind: null,
          source_asset_id: null,
        },
      ],
    };
    const createObjectUrl = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:safe");
    const revokeObjectUrl = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const onDownloadAttachment = vi.fn(async () => new Blob(["notes"]));
    const { container, unmount } = render(
      <ChatView
        {...baseProps}
        messages={[active, deleted]}
        onDownloadAttachment={onDownloadAttachment}
      />,
    );
    expect(screen.getByText(/<img src=x/)).toBeVisible();
    expect(screen.getByText("Deleted attachment")).toBeVisible();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("object")).toBeNull();
    expect(container.querySelector("iframe")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /<img src=x/ }));
    await waitFor(() => expect(onDownloadAttachment).toHaveBeenCalledOnce());
    expect(createObjectUrl).toHaveBeenCalledOnce();
    expect(revokeObjectUrl).toHaveBeenCalledWith("blob:safe");
    expect(click).toHaveBeenCalledOnce();
    unmount();
    expect(revokeObjectUrl).toHaveBeenCalledOnce();
    click.mockRestore();
    createObjectUrl.mockRestore();
    revokeObjectUrl.mockRestore();
  });
});
