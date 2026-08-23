import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Asset, IndexedDocument } from "../src/api/contracts";
import { ChatView } from "../src/features/chat/ChatView";
import { conversation, message } from "./fixtures";


const asset: Asset = {
  id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  original_filename: "notes.txt",
  media_type: "text/plain",
  byte_size: 5,
  content_sha256: "a".repeat(64),
  provenance_kind: "upload",
  source_asset_id: null,
  runtime_id: null,
  model_id: null,
  created_at: "2026-01-03T00:00:00Z",
  deleted_at: null,
};

const imageAsset: Asset = {
  ...asset,
  id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  original_filename: "sentinel-image.png",
  media_type: "image/png",
  byte_size: 12,
  content_sha256: "b".repeat(64),
};

const unsupportedImageAsset: Asset = {
  ...imageAsset,
  id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
  original_filename: "animated.gif",
  media_type: "image/gif",
};

const indexedDocument: IndexedDocument = {
  id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
  asset_id: asset.id,
  status: "ready",
  source_state: "active",
  original_filename: asset.original_filename,
  media_type: asset.media_type,
  chunk_count: 1,
  character_count: 5,
  failure_code: null,
  created_at: "2026-01-03T00:00:00Z",
  updated_at: "2026-01-03T00:00:01Z",
  completed_at: "2026-01-03T00:00:01Z",
};


function props(overrides = {}) {
  return {
    conversation,
    creatingNew: false,
    canGenerate: true,
    canUseVision: false,
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
    onIngestDocument: vi.fn(async () => indexedDocument),
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
    expect(await screen.findByText("Document ready")).toBeVisible();

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
    expect(await screen.findByText("Document ready")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Remove" }));

    await waitFor(() => expect(onDeleteAttachment).toHaveBeenCalledWith(asset.id, expect.any(AbortSignal)));
    expect(await screen.findByText("Deleted")).toBeVisible();
  });

  it("uses canonical uploaded metadata and blocks a non-vision model", async () => {
    const onGenerate = vi.fn(async () => undefined);
    render(
      <ChatView
        {...props({
          onUploadAttachment: vi.fn(async () => imageAsset),
          onGenerate,
        })}
      />,
    );

    await userEvent.upload(
      screen.getByLabelText("Attach files"),
      new File(["not-browser-trusted"], "misleading.txt", {
        type: "text/plain",
      }),
    );
    await userEvent.type(screen.getByLabelText("Message"), "inspect this");

    expect(await screen.findByText("Vision image")).toBeVisible();
    expect(
      screen.getByText("Select a vision-capable local model to send these images."),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    expect(onGenerate).not.toHaveBeenCalled();
  });

  it("sends only ordered asset IDs with a vision model and renders no preview", async () => {
    const onGenerate = vi.fn(async () => undefined);
    const { container } = render(
      <ChatView
        {...props({
          canUseVision: true,
          onUploadAttachment: vi.fn(async () => imageAsset),
          onGenerate,
        })}
      />,
    );

    await userEvent.upload(
      screen.getByLabelText("Attach files"),
      new File(["png"], "sentinel-image.png", { type: "image/png" }),
    );
    await userEvent.type(screen.getByLabelText("Message"), "inspect this");
    await screen.findByText("Vision image");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(onGenerate).toHaveBeenCalledWith("inspect this", [imageAsset.id]);
    expect(container.querySelector("img")).not.toBeInTheDocument();
    expect(container.querySelector("object")).not.toBeInTheDocument();
    expect(container.querySelector("iframe")).not.toBeInTheDocument();
    expect(container.innerHTML).not.toContain("data:image");
    expect(container.textContent?.toLowerCase()).not.toContain("base64");
  });

  it("rejects mixed vision and opaque attachments before generation", async () => {
    const onGenerate = vi.fn(async () => undefined);
    const onUploadAttachment = vi
      .fn()
      .mockResolvedValueOnce(imageAsset)
      .mockResolvedValueOnce(asset);
    render(
      <ChatView
        {...props({ canUseVision: true, onUploadAttachment, onGenerate })}
      />,
    );

    await userEvent.upload(screen.getByLabelText("Attach files"), [
      new File(["png"], "image.png"),
      new File(["notes"], "notes.txt"),
    ]);
    await userEvent.type(screen.getByLabelText("Message"), "inspect");

    expect(
      await screen.findByText(
        "Vision images cannot be submitted together with non-image attachments.",
      ),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    expect(onGenerate).not.toHaveBeenCalled();
  });

  it("rejects unsupported image metadata before generation", async () => {
    render(
      <ChatView
        {...props({
          canUseVision: true,
          onUploadAttachment: vi.fn(async () => unsupportedImageAsset),
        })}
      />,
    );

    await userEvent.upload(
      screen.getByLabelText("Attach files"),
      new File(["gif"], "animated.gif", { type: "image/gif" }),
    );
    await userEvent.type(screen.getByLabelText("Message"), "inspect");

    expect(
      await screen.findByText(
        "Only server-recognized PNG and JPEG images can use vision.",
      ),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });

  it("defers initial-conversation image generation", async () => {
    const onCreateConversation = vi.fn(async () => undefined);
    render(
      <ChatView
        {...props({
          conversation: null,
          creatingNew: true,
          canUseVision: true,
          onCreateConversation,
          onUploadAttachment: vi.fn(async () => imageAsset),
        })}
      />,
    );

    await userEvent.type(screen.getByLabelText("Your first message"), "inspect");
    await userEvent.upload(
      screen.getByLabelText("Attach files"),
      new File(["png"], "image.png"),
    );

    expect(
      await screen.findByText(/Image-assisted generation is available after you create/i),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Create and generate" }),
    ).toBeDisabled();
    expect(onCreateConversation).not.toHaveBeenCalled();
  });

  it("disables historical image replay while leaving filenames as text", () => {
    const last = {
      ...message(2, "user", "inspect"),
      attachments: [
        {
          id: imageAsset.id,
          position: 1,
          state: "active" as const,
          original_filename: "<img src=x onerror=alert(1)>.png",
          media_type: "image/png",
          byte_size: 12,
          provenance_kind: "upload" as const,
          source_asset_id: null,
        },
      ],
    };
    const { container } = render(
      <ChatView {...props({ canUseVision: true, messages: [last] })} />,
    );

    expect(
      screen.getByText(/historical image replay is not supported yet/i),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Generate response to last message" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("<img src=x onerror=alert(1)>.png")).toBeVisible();
    expect(container.querySelector("img")).not.toBeInTheDocument();
  });
});
