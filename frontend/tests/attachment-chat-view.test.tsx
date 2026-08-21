import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Asset } from "../src/api/contracts";
import { ChatView } from "../src/features/chat/ChatView";
import { conversation, message } from "./fixtures";


const asset: Asset = {
  id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  original_filename: "notes.txt",
  media_type: "text/plain",
  byte_size: 5,
  content_sha256: "a".repeat(64),
  created_at: "2026-01-03T00:00:00Z",
  deleted_at: null,
};


function props(overrides = {}) {
  return {
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
    onUploadAttachment: vi.fn(async () => asset),
    onDownloadAttachment: vi.fn(async () => new Blob()),
    onDeleteAttachment: vi.fn(async () => undefined),
    ...overrides,
  };
}


describe("attachment queue", () => {
  it("retries the same selected file with the same idempotency key", async () => {
    const onUploadAttachment = vi
      .fn()
      .mockRejectedValueOnce(new Error("ambiguous upload"))
      .mockResolvedValueOnce(asset);
    render(<ChatView {...props({ onUploadAttachment })} />);

    await userEvent.upload(
      screen.getByLabelText("Attach files"),
      new File(["notes"], "notes.txt", { type: "text/plain" }),
    );
    expect(await screen.findByText("Upload failed")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Retry upload" }));
    expect(await screen.findByText("Uploaded")).toBeVisible();

    expect(onUploadAttachment).toHaveBeenCalledTimes(2);
    expect(onUploadAttachment.mock.calls[0]?.[1]).toBe(
      onUploadAttachment.mock.calls[1]?.[1],
    );
  });

  it("shows progress, cancels through AbortSignal, and does not send unresolved assets", async () => {
    const onUploadAttachment = vi.fn(
      async (
        _file: File,
        _key: string,
        options: {
          signal?: AbortSignal;
          onProgress?: (progress: { loaded: number; total: number | null }) => void;
        },
      ) => {
        options.onProgress?.({ loaded: 2, total: 4 });
        return await new Promise<Asset>((_resolve, reject) => {
          options.signal?.addEventListener("abort", () => reject(new Error("cancelled")));
        });
      },
    );
    const onGenerate = vi.fn(async () => undefined);
    render(<ChatView {...props({ onUploadAttachment, onGenerate })} />);

    await userEvent.upload(
      screen.getByLabelText("Attach files"),
      new File(["notes"], "notes.txt"),
    );
    expect(await screen.findByText("Uploading 50%")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Cancel upload" }));
    expect(await screen.findByText("Cancelled")).toBeVisible();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    expect(onGenerate).not.toHaveBeenCalled();
  });

  it("removes an uploaded but unattached asset through authenticated deletion", async () => {
    const onDeleteAttachment = vi.fn(async () => undefined);
    render(<ChatView {...props({ onDeleteAttachment })} />);

    await userEvent.upload(
      screen.getByLabelText("Attach files"),
      new File(["notes"], "notes.txt"),
    );
    expect(await screen.findByText("Uploaded")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Remove" }));

    await waitFor(() => expect(onDeleteAttachment).toHaveBeenCalledWith(asset.id, expect.any(AbortSignal)));
    expect(await screen.findByText("Deleted")).toBeVisible();
  });
});
