import { render, screen } from "@testing-library/react";
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
});
