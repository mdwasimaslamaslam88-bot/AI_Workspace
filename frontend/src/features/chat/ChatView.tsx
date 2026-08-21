import {
  type ChangeEvent,
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import type { UploadProgress } from "../../api/client";
import type {
  Asset,
  ConversationCreateRequest,
  ConversationSummary,
  Message,
  MessageAttachment,
} from "../../api/contracts";
import { isVisionImageMediaType } from "../../app/collections";

export interface SafeNotice {
  message: string;
  status: number | null;
  requestId: string | null;
}

type QueueState =
  | "selected"
  | "uploading"
  | "uploaded"
  | "failed"
  | "cancelling"
  | "cancelled"
  | "attached"
  | "deleted";

interface QueuedAttachment {
  idempotencyKey: string;
  file: File;
  state: QueueState;
  progress: number | null;
  asset: Asset | null;
}

type UploadAttachment = (
  file: File,
  idempotencyKey: string,
  options: {
    signal?: AbortSignal;
    onProgress?: (value: UploadProgress) => void;
  },
) => Promise<Asset>;

interface AttachmentActions {
  onUploadAttachment: UploadAttachment;
  onDeleteAttachment: (assetId: string, signal?: AbortSignal) => Promise<void>;
}

interface ChatViewProps extends AttachmentActions {
  conversation: ConversationSummary | null;
  creatingNew: boolean;
  canGenerate: boolean;
  canUseVision?: boolean;
  messages: Message[];
  nextCursor: number | null;
  loadingMessages: boolean;
  loadingMoreMessages: boolean;
  creatingConversation: boolean;
  generating: boolean;
  notice: SafeNotice | null;
  onCreateConversation: (request: ConversationCreateRequest) => Promise<void>;
  onCancelNew: () => void;
  onGenerate: (userMessage?: string, attachmentIds?: string[]) => Promise<void>;
  onCancelGeneration: () => void;
  onLoadMoreMessages: () => void;
  onReloadMessages: () => void;
  onDownloadAttachment: (assetId: string, signal?: AbortSignal) => Promise<Blob>;
}

const EMPTY_ATTACHMENT_STATES = new Map<string, MessageAttachment["state"]>();

function formatTimestamp(timestamp: string): string {
  const date = new Date(timestamp);
  return Number.isNaN(date.valueOf()) ? "" : date.toLocaleString();
}

function queueStateLabel(item: QueuedAttachment): string {
  if (item.state === "uploading" && item.progress !== null) {
    return `Uploading ${item.progress}%`;
  }
  const labels: Record<QueueState, string> = {
    selected: "Selected",
    uploading: "Uploading",
    uploaded: "Uploaded",
    failed: "Upload failed",
    cancelling: "Cancelling",
    cancelled: "Cancelled",
    attached: "Attached",
    deleted: "Deleted",
  };
  return labels[item.state];
}

function useAttachmentQueue(
  actions: AttachmentActions,
  attachedStates: Map<string, MessageAttachment["state"]>,
) {
  const [items, setItems] = useState<QueuedAttachment[]>([]);
  const controllers = useRef(new Map<string, AbortController>());
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    const activeControllers = controllers.current;
    return () => {
      mounted.current = false;
      for (const controller of activeControllers.values()) controller.abort();
      activeControllers.clear();
    };
  }, []);

  const update = useCallback(
    (idempotencyKey: string, change: Partial<QueuedAttachment>) => {
      if (!mounted.current) return;
      setItems((current) =>
        current.map((item) =>
          item.idempotencyKey === idempotencyKey
            ? { ...item, ...change }
            : item,
        ),
      );
    },
    [],
  );

  const startUpload = useCallback(
    async (item: QueuedAttachment) => {
      const controller = new AbortController();
      controllers.current.set(item.idempotencyKey, controller);
      update(item.idempotencyKey, {
        state: "uploading",
        progress: 0,
      });
      try {
        const asset = await actions.onUploadAttachment(
          item.file,
          item.idempotencyKey,
          {
            signal: controller.signal,
            onProgress: ({ loaded, total }) => {
              if (total === null || total <= 0) {
                update(item.idempotencyKey, { progress: null });
                return;
              }
              update(item.idempotencyKey, {
                progress: Math.min(100, Math.floor((loaded / total) * 100)),
              });
            },
          },
        );
        update(item.idempotencyKey, {
          asset,
          progress: 100,
          state: "uploaded",
        });
      } catch {
        update(item.idempotencyKey, {
          progress: null,
          state: controller.signal.aborted ? "cancelled" : "failed",
        });
      } finally {
        if (controllers.current.get(item.idempotencyKey) === controller) {
          controllers.current.delete(item.idempotencyKey);
        }
      }
    },
    [actions, update],
  );

  const selectFiles = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      const selected = Array.from(event.target.files ?? []).map((file) => ({
        idempotencyKey: crypto.randomUUID(),
        file,
        state: "selected" as const,
        progress: null,
        asset: null,
      }));
      event.target.value = "";
      if (selected.length === 0) return;
      setItems((current) => [...current, ...selected]);
      for (const item of selected) void startUpload(item);
    },
    [startUpload],
  );

  const cancel = useCallback((item: QueuedAttachment) => {
    setItems((current) =>
      current.map((candidate) =>
        candidate.idempotencyKey === item.idempotencyKey
          ? { ...candidate, state: "cancelling" }
          : candidate,
      ),
    );
    controllers.current.get(item.idempotencyKey)?.abort();
  }, []);

  const retry = useCallback(
    (item: QueuedAttachment) => void startUpload(item),
    [startUpload],
  );

  const remove = useCallback(
    async (item: QueuedAttachment) => {
      if (item.state === "selected") {
        setItems((current) =>
          current.filter(
            (candidate) => candidate.idempotencyKey !== item.idempotencyKey,
          ),
        );
        return;
      }
      if (item.state !== "uploaded" || item.asset === null) return;
      const controller = new AbortController();
      controllers.current.set(item.idempotencyKey, controller);
      update(item.idempotencyKey, { state: "cancelling" });
      try {
        await actions.onDeleteAttachment(item.asset.id, controller.signal);
        update(item.idempotencyKey, { state: "deleted" });
      } catch {
        update(item.idempotencyKey, { state: "uploaded" });
      } finally {
        if (controllers.current.get(item.idempotencyKey) === controller) {
          controllers.current.delete(item.idempotencyKey);
        }
      }
    },
    [actions, update],
  );

  useEffect(() => {
    setItems((current) => {
      let changed = false;
      const reconciled = current.map((item) => {
        if (item.asset === null) return item;
        const serverState = attachedStates.get(item.asset.id);
        const state =
          serverState === "deleted"
            ? "deleted"
            : serverState === "active"
              ? "attached"
              : item.state;
        if (state === item.state) return item;
        changed = true;
        return { ...item, state };
      });
      return changed ? reconciled : current;
    });
  }, [attachedStates]);

  const readyAssets = items.flatMap((item) =>
    item.state === "uploaded" && item.asset !== null ? [item.asset] : [],
  );
  const readyAssetIds = readyAssets.map((asset) => asset.id);
  const unresolved = items.some((item) =>
    ["selected", "uploading", "failed", "cancelling", "cancelled"].includes(
      item.state,
    ),
  );

  return {
    items,
    readyAssets,
    readyAssetIds,
    unresolved,
    selectFiles,
    cancel,
    retry,
    remove,
  };
}

function AttachmentPicker({
  queue,
  disabled,
}: {
  queue: ReturnType<typeof useAttachmentQueue>;
  disabled: boolean;
}) {
  return (
    <div className="attachment-picker">
      <label className="button button-secondary attachment-select">
        Attach files
        <input
          type="file"
          multiple
          onChange={queue.selectFiles}
          disabled={disabled}
        />
      </label>
      {queue.items.length > 0 && (
        <ul className="attachment-queue" aria-label="Selected attachments">
          {queue.items.map((item) => (
            <li key={item.idempotencyKey}>
              <span className="attachment-name">{item.file.name}</span>
              {item.asset !== null &&
                isVisionImageMediaType(item.asset.media_type) && (
                  <span className="attachment-kind">Vision image</span>
                )}
              <span className="attachment-state" role="status">
                {queueStateLabel(item)}
              </span>
              {item.state === "uploading" && item.progress !== null && (
                <progress value={item.progress} max={100}>
                  {item.progress}%
                </progress>
              )}
              {item.state === "uploading" && (
                <button
                  type="button"
                  className="button button-quiet"
                  onClick={() => queue.cancel(item)}
                >
                  Cancel upload
                </button>
              )}
              {(item.state === "failed" || item.state === "cancelled") && (
                <button
                  type="button"
                  className="button button-quiet"
                  onClick={() => queue.retry(item)}
                  disabled={disabled}
                >
                  Retry upload
                </button>
              )}
              {(item.state === "selected" || item.state === "uploaded") && (
                <button
                  type="button"
                  className="button button-quiet"
                  onClick={() => void queue.remove(item)}
                  disabled={disabled}
                >
                  Remove
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function NewConversationView({
  canGenerate,
  creating,
  notice,
  onCreate,
  onCancel,
  onUploadAttachment,
  onDeleteAttachment,
}: {
  canGenerate: boolean;
  creating: boolean;
  notice: SafeNotice | null;
  onCreate: (request: ConversationCreateRequest) => Promise<void>;
  onCancel: () => void;
  onUploadAttachment: UploadAttachment;
  onDeleteAttachment: AttachmentActions["onDeleteAttachment"];
}) {
  const [title, setTitle] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [initialMessage, setInitialMessage] = useState("");
  const queue = useAttachmentQueue(
    { onUploadAttachment, onDeleteAttachment },
    EMPTY_ATTACHMENT_STATES,
  );
  const hasInitialImage = queue.readyAssets.some((asset) =>
    asset.media_type.startsWith("image/"),
  );

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (
      !initialMessage.trim() ||
      !canGenerate ||
      queue.unresolved ||
      hasInitialImage
    ) {
      return;
    }
    const request: ConversationCreateRequest = {
      initial_message: initialMessage,
    };
    if (title.trim()) request.title = title;
    if (systemPrompt.trim()) request.system_prompt = systemPrompt;
    if (queue.readyAssetIds.length > 0) {
      request.attachment_ids = queue.readyAssetIds;
    }
    await onCreate(request);
  }

  return (
    <section className="new-conversation" aria-labelledby="new-conversation-title">
      <div className="chat-heading">
        <div>
          <p className="eyebrow">New conversation</p>
          <h2 id="new-conversation-title">Start with a prompt</h2>
        </div>
        <button className="button button-quiet" onClick={onCancel}>Cancel</button>
      </div>
      <form className="new-conversation-form" onSubmit={(event) => void submit(event)}>
        <div>
          <label htmlFor="conversation-title">Title <span>(optional)</span></label>
          <input
            id="conversation-title"
            value={title}
            maxLength={255}
            onChange={(event) => setTitle(event.target.value)}
            disabled={creating}
          />
        </div>
        <div>
          <label htmlFor="system-prompt">System prompt <span>(optional)</span></label>
          <textarea
            id="system-prompt"
            value={systemPrompt}
            maxLength={100000}
            rows={4}
            onChange={(event) => setSystemPrompt(event.target.value)}
            disabled={creating}
          />
        </div>
        <div>
          <label htmlFor="initial-message">Your first message</label>
          <textarea
            id="initial-message"
            value={initialMessage}
            maxLength={100000}
            rows={7}
            required
            autoFocus
            onChange={(event) => setInitialMessage(event.target.value)}
            disabled={creating}
          />
        </div>
        <AttachmentPicker queue={queue} disabled={creating} />
        {hasInitialImage && (
          <p className="vision-guidance" role="status">
            Image-assisted generation is available after you create or open a
            conversation. Remove the image to continue here.
          </p>
        )}
        {notice !== null && (
          <div className="notice notice-error" role="alert">
            <p>{notice.message}</p>
            <div className="diagnostics">
              {notice.status !== null && <span>HTTP {notice.status}</span>}
              {notice.requestId !== null && <span>Request {notice.requestId}</span>}
            </div>
          </div>
        )}
        {!canGenerate && (
          <p className="notice notice-error" role="alert">
            Select an available local text model before starting.
          </p>
        )}
        <button
          className="button button-primary"
          disabled={
            creating ||
            !canGenerate ||
            !initialMessage.trim() ||
            queue.unresolved ||
            hasInitialImage
          }
        >
          {creating ? "Creating…" : "Create and generate"}
        </button>
      </form>
    </section>
  );
}

export function ChatView({
  conversation,
  creatingNew,
  canGenerate,
  canUseVision = false,
  messages,
  nextCursor,
  loadingMessages,
  loadingMoreMessages,
  creatingConversation,
  generating,
  notice,
  onCreateConversation,
  onCancelNew,
  onGenerate,
  onCancelGeneration,
  onLoadMoreMessages,
  onReloadMessages,
  onUploadAttachment,
  onDownloadAttachment,
  onDeleteAttachment,
}: ChatViewProps) {
  const [draft, setDraft] = useState("");
  const [attachmentNotice, setAttachmentNotice] = useState<string | null>(null);
  const objectUrls = useRef(new Set<string>());
  const contentControllers = useRef(new Set<AbortController>());
  const attachedStates = useMemo(
    () =>
      new Map(
        messages.flatMap((message) =>
          message.attachments.map((attachment) => [
            attachment.id,
            attachment.state,
          ] as const),
        ),
      ),
    [messages],
  );
  const queue = useAttachmentQueue(
    { onUploadAttachment, onDeleteAttachment },
    attachedStates,
  );
  const readyVisionImages = queue.readyAssets.filter((asset) =>
    isVisionImageMediaType(asset.media_type),
  );
  const readyUnsupportedImages = queue.readyAssets.filter(
    (asset) =>
      asset.media_type.startsWith("image/") &&
      !isVisionImageMediaType(asset.media_type),
  );
  const readyOpaqueAttachments = queue.readyAssets.filter(
    (asset) => !asset.media_type.startsWith("image/"),
  );
  let visionBlockReason: string | null = null;
  if (readyUnsupportedImages.length > 0) {
    visionBlockReason = "Only server-recognized PNG and JPEG images can use vision.";
  } else if (
    readyVisionImages.length > 0 &&
    readyOpaqueAttachments.length > 0
  ) {
    visionBlockReason =
      "Vision images cannot be submitted together with non-image attachments.";
  } else if (readyVisionImages.length > 0 && !canUseVision) {
    visionBlockReason = "Select a vision-capable local model to send these images.";
  }

  useEffect(() => {
    const urls = objectUrls.current;
    const controllers = contentControllers.current;
    return () => {
      for (const controller of controllers) controller.abort();
      controllers.clear();
      for (const url of urls) URL.revokeObjectURL(url);
      urls.clear();
    };
  }, []);

  const download = useCallback(
    async (attachment: MessageAttachment) => {
      const controller = new AbortController();
      contentControllers.current.add(controller);
      let objectUrl: string | null = null;
      setAttachmentNotice(null);
      try {
        const blob = await onDownloadAttachment(attachment.id, controller.signal);
        objectUrl = URL.createObjectURL(blob);
        objectUrls.current.add(objectUrl);
        const link = document.createElement("a");
        link.href = objectUrl;
        link.download = attachment.original_filename ?? "attachment";
        link.rel = "noopener";
        link.click();
      } catch {
        if (!controller.signal.aborted) {
          setAttachmentNotice("The attachment could not be downloaded.");
        }
      } finally {
        contentControllers.current.delete(controller);
        if (objectUrl !== null) {
          URL.revokeObjectURL(objectUrl);
          objectUrls.current.delete(objectUrl);
        }
      }
    },
    [onDownloadAttachment],
  );

  const deletePersistedAttachment = useCallback(
    async (attachment: MessageAttachment) => {
      const controller = new AbortController();
      contentControllers.current.add(controller);
      setAttachmentNotice(null);
      try {
        await onDeleteAttachment(attachment.id, controller.signal);
      } catch {
        if (!controller.signal.aborted) {
          setAttachmentNotice("The attachment could not be deleted.");
        }
      } finally {
        contentControllers.current.delete(controller);
      }
    },
    [onDeleteAttachment],
  );

  if (creatingNew) {
    return (
      <NewConversationView
        canGenerate={canGenerate}
        creating={creatingConversation}
        notice={notice}
        onCreate={onCreateConversation}
        onCancel={onCancelNew}
        onUploadAttachment={onUploadAttachment}
        onDeleteAttachment={onDeleteAttachment}
      />
    );
  }

  if (conversation === null) {
    return (
      <section className="empty-chat">
        <p className="eyebrow">Ready when you are</p>
        <h2>Choose a conversation or start a new one.</h2>
        <p className="muted">
          Your backend remains the source of truth for identity, history, and
          local model execution.
        </p>
      </section>
    );
  }

  const lastMessage = messages.at(-1);
  const historicalVisionReplayRequired =
    lastMessage?.attachments.some(
      (attachment) =>
        attachment.state === "deleted" ||
        isVisionImageMediaType(attachment.media_type),
    ) ?? false;
  const canRetryResponse =
    lastMessage?.role === "user" && !historicalVisionReplayRequired;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (
      !draft.trim() ||
      generating ||
      !canGenerate ||
      queue.unresolved ||
      visionBlockReason !== null
    ) {
      return;
    }
    const message = draft;
    const attachmentIds = queue.readyAssetIds;
    setDraft("");
    if (attachmentIds.length > 0) {
      await onGenerate(message, attachmentIds);
    } else {
      await onGenerate(message);
    }
  }

  return (
    <section className="chat-view" aria-labelledby="conversation-heading">
      <header className="chat-heading">
        <div>
          <p className="eyebrow">Conversation</p>
          <h2 id="conversation-heading">
            {conversation.title ?? `Conversation ${conversation.id.slice(0, 8)}`}
          </h2>
        </div>
        <button
          className="button button-quiet"
          onClick={onReloadMessages}
          disabled={loadingMessages || generating}
        >
          Reload
        </button>
      </header>

      {notice !== null && (
        <div className="notice notice-error" role="alert">
          <p>{notice.message}</p>
          <div className="diagnostics">
            {notice.status !== null && <span>HTTP {notice.status}</span>}
            {notice.requestId !== null && <span>Request {notice.requestId}</span>}
          </div>
        </div>
      )}
      {attachmentNotice !== null && (
        <p className="notice notice-error" role="alert">{attachmentNotice}</p>
      )}

      <div className="message-region" aria-live="polite" aria-busy={loadingMessages}>
        {loadingMessages && <p className="muted" role="status">Loading history…</p>}
        {!loadingMessages && messages.length === 0 && (
          <p className="empty-copy">This conversation has no messages.</p>
        )}
        <ol className="message-list">
          {messages.map((message) => (
            <li className={`message message-${message.role}`} key={message.id}>
              <div className="message-meta">
                <strong>{message.role}</strong>
                <time dateTime={message.created_at}>
                  {formatTimestamp(message.created_at)}
                </time>
              </div>
              <p>{message.content}</p>
              {message.attachments.length > 0 && (
                <ul className="message-attachments" aria-label="Message attachments">
                  {message.attachments.map((attachment) => (
                    <li key={attachment.id}>
                      {attachment.state === "deleted" ? (
                        <span className="attachment-tombstone">Deleted attachment</span>
                      ) : (
                        <>
                          <button
                            type="button"
                            className="attachment-download"
                            onClick={() => void download(attachment)}
                          >
                            {attachment.original_filename ?? "Attachment"}
                          </button>
                          <span className="attachment-details">
                            {attachment.media_type} · {attachment.byte_size} bytes
                          </span>
                          {isVisionImageMediaType(attachment.media_type) && (
                            <span className="attachment-kind">Vision image</span>
                          )}
                          <button
                            type="button"
                            className="button button-quiet"
                            onClick={() => void deletePersistedAttachment(attachment)}
                          >
                            Delete
                          </button>
                        </>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ol>
        {nextCursor !== null && (
          <button
            className="button button-secondary load-messages"
            onClick={onLoadMoreMessages}
            disabled={loadingMoreMessages || generating}
          >
            {loadingMoreMessages ? "Loading…" : "Load more messages"}
          </button>
        )}
      </div>

      {canRetryResponse && !generating && (
        <button
          className="button button-secondary retry-response"
          onClick={() => void onGenerate()}
          disabled={!canGenerate}
        >
          Generate response to last message
        </button>
      )}
      {lastMessage?.role === "user" &&
        historicalVisionReplayRequired &&
        !generating && (
          <p className="vision-guidance" role="status">
            Response retry is unavailable because historical image replay is not
            supported yet. Send a new message with the image instead.
          </p>
        )}

      <form className="composer" onSubmit={(event) => void submit(event)}>
        <label className="sr-only" htmlFor="chat-prompt">Message</label>
        <textarea
          id="chat-prompt"
          rows={3}
          maxLength={100000}
          value={draft}
          placeholder="Message your local AI"
          onChange={(event) => setDraft(event.target.value)}
          disabled={generating}
        />
        <AttachmentPicker queue={queue} disabled={generating} />
        {visionBlockReason !== null && (
          <p className="vision-guidance" role="status">
            {visionBlockReason}
          </p>
        )}
        <div className="composer-actions">
          {generating ? (
            <>
              <span role="status">Generating a persisted response…</span>
              <button
                type="button"
                className="button button-secondary"
                onClick={onCancelGeneration}
              >
                Cancel
              </button>
            </>
          ) : (
            <button
              className="button button-primary"
              disabled={
                !canGenerate ||
                !draft.trim() ||
                queue.unresolved ||
                visionBlockReason !== null
              }
            >
              Send
            </button>
          )}
        </div>
      </form>
    </section>
  );
}
