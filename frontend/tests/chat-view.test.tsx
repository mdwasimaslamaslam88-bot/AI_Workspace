import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { mergeMessages } from "../src/app/collections";
import { ChatView, type SafeNotice } from "../src/features/chat/ChatView";
import { conversation, message, rawSecret } from "./fixtures";

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
  onCancelGeneration: vi.fn(),
  onLoadMoreMessages: vi.fn(),
  onReloadMessages: vi.fn(),
  onUploadAttachment: vi.fn(async () => Promise.reject(new Error("unused"))),
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
    expect(await screen.findByText("Uploaded")).toBeVisible();
    expect(onUploadAttachment.mock.calls[0]?.[1]).toMatch(
      /^[0-9a-f-]{36}$/,
    );
    await userEvent.type(screen.getByLabelText("Message"), "Use the notes");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(onGenerate).toHaveBeenCalledWith("Use the notes", [asset.id]);
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
