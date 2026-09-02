import {
  type ChangeEvent,
  type DragEvent,
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { PresenceState } from "@work-station/shared";

import type { UploadProgress } from "../../api/client";
import type {
  Asset,
  ConversationCreateRequest,
  ConversationSummary,
  IndexedDocument,
  Message,
  MessageCitation,
  MessageAttachment,
} from "../../api/contracts";
import { isDocumentMediaType, isVisionImageMediaType } from "../../app/collections";
import { MessageContent } from "./MessageContent";

export interface SafeNotice {
  message: string;
  status: number | null;
  requestId: string | null;
}

type QueueState =
  | "selected"
  | "uploading"
  | "indexing"
  | "indexed"
  | "index_failed"
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
  onIngestDocument: (
    assetId: string,
    signal?: AbortSignal,
  ) => Promise<IndexedDocument>;
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
  onEditAndResend?: (message: Message, content: string) => Promise<void>;
  onRegenerate?: (lastUserMessage: Message) => Promise<void>;
  onCancelGeneration: () => void;
  onLoadMoreMessages: () => void;
  onReloadMessages: () => void;
  onDownloadAttachment: (assetId: string, signal?: AbortSignal) => Promise<Blob>;
  voiceInputAvailable?: boolean;
  voiceOutputAvailable?: boolean;
  onTranscribeVoice?: (
    recording: Blob,
    signal?: AbortSignal,
  ) => Promise<string>;
  onSynthesizeVoice?: (
    text: string,
    signal?: AbortSignal,
  ) => Promise<{ asset: Asset; audio: Blob }>;
  imageGenerationAvailable?: boolean;
  imageEditingAvailable?: boolean;
  onGenerateImage?: (prompt: string) => Promise<void>;
  onEditImage?: (sourceAssetId: string, instruction: string) => Promise<void>;
  onPresenceStateChange?: (state: PresenceState | null) => void;
  onCreateMission?: (goal: string, source: "text" | "voice") => Promise<void>;
}

const EMPTY_ATTACHMENT_STATES = new Map<string, MessageAttachment["state"]>();
const MAX_VOICE_CAPTURE_BYTES = 220_000;

function formatTimestamp(timestamp: string): string {
  const date = new Date(timestamp);
  return Number.isNaN(date.valueOf()) ? "" : date.toLocaleString();
}

function citationLocation(citation: MessageCitation): string {
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

function queueStateLabel(item: QueuedAttachment): string {
  if (item.state === "uploading" && item.progress !== null) {
    return `Uploading ${item.progress}%`;
  }
  const labels: Record<QueueState, string> = {
    selected: "Selected",
    uploading: "Uploading",
    uploaded: "Uploaded",
    indexing: "Indexing document",
    indexed: "Document ready",
    index_failed: "Document indexing failed",
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
      let uploadedAsset: Asset | null = null;
      controllers.current.set(item.idempotencyKey, controller);
      update(item.idempotencyKey, {
        state: "uploading",
        progress: 0,
      });
      try {
        uploadedAsset = await actions.onUploadAttachment(
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
        if (isDocumentMediaType(uploadedAsset.media_type)) {
          update(item.idempotencyKey, {
            asset: uploadedAsset,
            progress: 100,
            state: "indexing",
          });
          await actions.onIngestDocument(uploadedAsset.id, controller.signal);
          update(item.idempotencyKey, { state: "indexed" });
        } else {
          update(item.idempotencyKey, {
            asset: uploadedAsset,
            progress: 100,
            state: "uploaded",
          });
        }
      } catch {
        update(item.idempotencyKey, {
          asset: uploadedAsset ?? item.asset,
          progress: null,
          state: controller.signal.aborted
            ? "cancelled"
            : uploadedAsset === null
              ? "failed"
              : "index_failed",
        });
      } finally {
        if (controllers.current.get(item.idempotencyKey) === controller) {
          controllers.current.delete(item.idempotencyKey);
        }
      }
    },
    [actions, update],
  );

  const addFiles = useCallback(
    (files: File[]) => {
      const selected = files.map((file) => ({
        idempotencyKey: crypto.randomUUID(),
        file,
        state: "selected" as const,
        progress: null,
        asset: null,
      }));
      if (selected.length === 0) return;
      setItems((current) => [...current, ...selected]);
      for (const item of selected) void startUpload(item);
    },
    [startUpload],
  );

  const selectFiles = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(event.target.files ?? []);
      event.target.value = "";
      addFiles(files);
    },
    [addFiles],
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
      if (
        !["uploaded", "indexed", "index_failed"].includes(item.state) ||
        item.asset === null
      ) return;
      const previousState = item.state;
      const controller = new AbortController();
      controllers.current.set(item.idempotencyKey, controller);
      update(item.idempotencyKey, { state: "cancelling" });
      try {
        await actions.onDeleteAttachment(item.asset.id, controller.signal);
        update(item.idempotencyKey, { state: "deleted" });
      } catch {
        update(item.idempotencyKey, { state: previousState });
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
    ["uploaded", "indexed"].includes(item.state) && item.asset !== null
      ? [item.asset]
      : [],
  );
  const readyAssetIds = readyAssets.map((asset) => asset.id);
  const unresolved = items.some((item) =>
    [
      "selected",
      "uploading",
      "indexing",
      "failed",
      "index_failed",
      "cancelling",
      "cancelled",
    ].includes(item.state),
  );

  return {
    items,
    readyAssets,
    readyAssetIds,
    unresolved,
    addFiles,
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
  const [draggingFiles, setDraggingFiles] = useState(false);

  function includesFiles(event: DragEvent<HTMLDivElement>): boolean {
    return Array.from(event.dataTransfer.types).includes("Files");
  }

  function dragOver(event: DragEvent<HTMLDivElement>) {
    if (disabled || !includesFiles(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    setDraggingFiles(true);
  }

  function dragLeave(event: DragEvent<HTMLDivElement>) {
    const nextTarget = event.relatedTarget;
    if (nextTarget instanceof Node && event.currentTarget.contains(nextTarget)) return;
    setDraggingFiles(false);
  }

  function drop(event: DragEvent<HTMLDivElement>) {
    if (!includesFiles(event)) return;
    event.preventDefault();
    setDraggingFiles(false);
    if (disabled) return;
    queue.addFiles(Array.from(event.dataTransfer.files));
  }

  return (
    <div
      className={`attachment-picker${draggingFiles ? " attachment-picker-dragging" : ""}`}
      aria-label="File attachments"
      aria-disabled={disabled}
      onDragEnter={dragOver}
      onDragOver={dragOver}
      onDragLeave={dragLeave}
      onDrop={drop}
    >
      <label className="button button-secondary attachment-select">
        Attach files
        <input
          type="file"
          multiple
          onChange={queue.selectFiles}
          disabled={disabled}
        />
      </label>
      <span className="attachment-drop-hint">or drop files here</span>
      {queue.items.length > 0 && (
        <ul className="attachment-queue" aria-label="Selected attachments">
          {queue.items.map((item) => (
            <li key={item.idempotencyKey}>
              <span className="attachment-name">{item.file.name}</span>
              {item.asset !== null &&
                isVisionImageMediaType(item.asset.media_type) && (
                  <span className="attachment-kind">Vision image</span>
                )}
              {item.asset !== null &&
                isDocumentMediaType(item.asset.media_type) && (
                  <span className="attachment-kind">Searchable document</span>
                )}
              <span className="attachment-state" role="status">
                {queueStateLabel(item)}
              </span>
              {item.state === "uploading" && item.progress !== null && (
                <progress value={item.progress} max={100}>
                  {item.progress}%
                </progress>
              )}
              {(item.state === "uploading" || item.state === "indexing") && (
                <button
                  type="button"
                  className="button button-quiet"
                  onClick={() => queue.cancel(item)}
                >
                  {item.state === "indexing" ? "Cancel indexing" : "Cancel upload"}
                </button>
              )}
              {(item.state === "failed" ||
                item.state === "index_failed" ||
                item.state === "cancelled") && (
                <button
                  type="button"
                  className="button button-quiet"
                  onClick={() => queue.retry(item)}
                  disabled={disabled}
                >
                  {item.state === "index_failed" ? "Retry indexing" : "Retry upload"}
                </button>
              )}
              {(
                ["selected", "uploaded", "indexed", "index_failed"] as QueueState[]
              ).includes(item.state) && (
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

function OwnedImagePreview({
  assetId,
  label,
  onDownload,
}: {
  assetId: string;
  label: string;
  onDownload: ChatViewProps["onDownloadAttachment"];
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | null = null;
    setUrl(null);
    setFailed(false);
    void onDownload(assetId, controller.signal)
      .then((blob) => {
        if (controller.signal.aborted) return;
        if (!["image/png", "image/jpeg"].includes(blob.type)) {
          throw new Error("Unsupported image response");
        }
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch(() => {
        if (!controller.signal.aborted) setFailed(true);
      });
    return () => {
      controller.abort();
      if (objectUrl !== null) URL.revokeObjectURL(objectUrl);
    };
  }, [assetId, onDownload]);

  if (failed) return <span className="muted">Image preview unavailable.</span>;
  if (url === null) return <span className="muted" role="status">Loading image…</span>;
  return <img className="owned-image-preview" src={url} alt={label} />;
}

function NewConversationView({
  canGenerate,
  creating,
  notice,
  onCreate,
  onCancel,
  onUploadAttachment,
  onIngestDocument,
  onDeleteAttachment,
}: {
  canGenerate: boolean;
  creating: boolean;
  notice: SafeNotice | null;
  onCreate: (request: ConversationCreateRequest) => Promise<void>;
  onCancel: () => void;
  onUploadAttachment: UploadAttachment;
  onIngestDocument: AttachmentActions["onIngestDocument"];
  onDeleteAttachment: AttachmentActions["onDeleteAttachment"];
}) {
  const [title, setTitle] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [initialMessage, setInitialMessage] = useState("");
  const queue = useAttachmentQueue(
    { onUploadAttachment, onIngestDocument, onDeleteAttachment },
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
  onEditAndResend,
  onRegenerate,
  onCancelGeneration,
  onLoadMoreMessages,
  onReloadMessages,
  onUploadAttachment,
  onIngestDocument,
  onDownloadAttachment,
  onDeleteAttachment,
  voiceInputAvailable = false,
  voiceOutputAvailable = false,
  onTranscribeVoice,
  onSynthesizeVoice,
  imageGenerationAvailable = false,
  imageEditingAvailable = false,
  onGenerateImage,
  onEditImage,
  onPresenceStateChange,
  onCreateMission,
}: ChatViewProps) {
  const [draft, setDraft] = useState("");
  const [draftSource, setDraftSource] = useState<"text" | "voice">("text");
  const [missionSubmitting, setMissionSubmitting] = useState(false);
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
  const [editedMessageContent, setEditedMessageContent] = useState("");
  const [attachmentNotice, setAttachmentNotice] = useState<string | null>(null);
  const [voiceNotice, setVoiceNotice] = useState<string | null>(null);
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [synthesizingMessageId, setSynthesizingMessageId] = useState<string | null>(null);
  const [imagePrompt, setImagePrompt] = useState("");
  const [editInstruction, setEditInstruction] = useState("");
  const [editingAttachment, setEditingAttachment] =
    useState<MessageAttachment | null>(null);
  const [imageOperation, setImageOperation] = useState<
    "generating" | "editing" | null
  >(null);
  const [speechOutput, setSpeechOutput] = useState<{
    messageId: string;
    asset: Asset;
    url: string;
  } | null>(null);

  useEffect(() => {
    setEditingMessageId(null);
    setEditedMessageContent("");
  }, [conversation?.id]);
  const objectUrls = useRef(new Set<string>());
  const contentControllers = useRef(new Set<AbortController>());
  const recorder = useRef<MediaRecorder | null>(null);
  const recordingStream = useRef<MediaStream | null>(null);
  const recordingTimer = useRef<number | null>(null);
  const voiceController = useRef<AbortController | null>(null);
  const voiceUploadInput = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (recording) onPresenceStateChange?.("LISTENING");
    else if (transcribing) onPresenceStateChange?.("THINKING");
    else if (
      missionSubmitting ||
      synthesizingMessageId !== null ||
      imageOperation !== null
    ) {
      onPresenceStateChange?.("WORKING");
    } else onPresenceStateChange?.(null);
  }, [
    imageOperation,
    missionSubmitting,
    onPresenceStateChange,
    recording,
    synthesizingMessageId,
    transcribing,
  ]);
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
    { onUploadAttachment, onIngestDocument, onDeleteAttachment },
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
      voiceController.current?.abort();
      if (recordingTimer.current !== null) window.clearTimeout(recordingTimer.current);
      if (recorder.current !== null) {
        recorder.current.ondataavailable = null;
        recorder.current.onstop = null;
        if (recorder.current.state === "recording") recorder.current.stop();
      }
      for (const track of recordingStream.current?.getTracks() ?? []) track.stop();
    };
  }, []);

  const stopRecording = useCallback(() => {
    if (recordingTimer.current !== null) {
      window.clearTimeout(recordingTimer.current);
      recordingTimer.current = null;
    }
    if (recorder.current?.state === "recording") recorder.current.stop();
  }, []);

  const transcribeAudio = useCallback(
    (audio: Blob) => {
      if (
        !voiceInputAvailable ||
        onTranscribeVoice === undefined ||
        audio.size === 0 ||
        audio.size > MAX_VOICE_CAPTURE_BYTES ||
        recording ||
        transcribing ||
        synthesizingMessageId !== null
      ) return;
      const controller = new AbortController();
      voiceController.current = controller;
      setVoiceNotice(null);
      setTranscribing(true);
      void onTranscribeVoice(audio, controller.signal)
        .then((transcript) => {
          setDraft(transcript);
          setDraftSource("voice");
        })
        .catch(() => {
          if (!controller.signal.aborted) {
            setVoiceNotice("The local audio could not be transcribed.");
          }
        })
        .finally(() => {
          if (voiceController.current === controller) voiceController.current = null;
          setTranscribing(false);
        });
    },
    [
      onTranscribeVoice,
      recording,
      synthesizingMessageId,
      transcribing,
      voiceInputAvailable,
    ],
  );

  const uploadVoice = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0] ?? null;
      event.target.value = "";
      if (file === null) return;
      const mediaType = file.type.split(";", 1)[0]?.toLowerCase() ?? "";
      const extension = file.name.toLowerCase().split(".").pop() ?? "";
      if (
        ![
          "audio/wav",
          "audio/x-wav",
          "audio/ogg",
          "audio/mpeg",
          "audio/mp3",
          "audio/webm",
        ].includes(mediaType) &&
        !(mediaType === "" && ["wav", "ogg", "mp3", "webm"].includes(extension))
      ) {
        setVoiceNotice("Choose a WAV, OGG, MP3, or WebM audio file.");
        return;
      }
      if (file.size === 0 || file.size > MAX_VOICE_CAPTURE_BYTES) {
        setVoiceNotice("The audio file exceeded its local size limit.");
        return;
      }
      transcribeAudio(file);
    },
    [transcribeAudio],
  );

  const startRecording = useCallback(async () => {
    if (
      !voiceInputAvailable ||
      onTranscribeVoice === undefined ||
      recording ||
      transcribing ||
      synthesizingMessageId !== null
    ) return;
    setVoiceNotice(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      recordingStream.current = stream;
      const preferred = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus"].find(
        (value) => MediaRecorder.isTypeSupported(value),
      );
      const mediaRecorder = new MediaRecorder(stream, {
        ...(preferred === undefined ? {} : { mimeType: preferred }),
        audioBitsPerSecond: 32_000,
      });
      recorder.current = mediaRecorder;
      const chunks: Blob[] = [];
      let totalBytes = 0;
      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size === 0) return;
        totalBytes += event.data.size;
        if (totalBytes > MAX_VOICE_CAPTURE_BYTES) {
          setVoiceNotice("The recording exceeded its local size limit.");
          stopRecording();
          return;
        }
        chunks.push(event.data);
      };
      mediaRecorder.onstop = () => {
        setRecording(false);
        for (const track of stream.getTracks()) track.stop();
        recordingStream.current = null;
        recorder.current = null;
        if (chunks.length === 0 || totalBytes > MAX_VOICE_CAPTURE_BYTES) return;
        const blob = new Blob(chunks, { type: mediaRecorder.mimeType });
        transcribeAudio(blob);
      };
      mediaRecorder.start(250);
      setRecording(true);
      recordingTimer.current = window.setTimeout(stopRecording, 12_000);
    } catch {
      setVoiceNotice("Microphone access is unavailable.");
      for (const track of recordingStream.current?.getTracks() ?? []) track.stop();
      recordingStream.current = null;
      setRecording(false);
    }
  }, [
    onTranscribeVoice,
    recording,
    stopRecording,
    synthesizingMessageId,
    transcribeAudio,
    transcribing,
    voiceInputAvailable,
  ]);

  const synthesizeMessage = useCallback(
    async (message: Message) => {
      if (
        !voiceOutputAvailable ||
        onSynthesizeVoice === undefined ||
        recording ||
        transcribing
      ) return;
      voiceController.current?.abort();
      const controller = new AbortController();
      voiceController.current = controller;
      setVoiceNotice(null);
      setSynthesizingMessageId(message.id);
      try {
        const result = await onSynthesizeVoice(message.content, controller.signal);
        if (speechOutput !== null) {
          try {
            await onDeleteAttachment(speechOutput.asset.id);
          } catch {
            // A superseded output remains owner-scoped if its best-effort
            // cleanup fails; the new playable result remains valid.
          }
          URL.revokeObjectURL(speechOutput.url);
          objectUrls.current.delete(speechOutput.url);
        }
        const typedAudio = new Blob([result.audio], { type: result.asset.media_type });
        const url = URL.createObjectURL(typedAudio);
        objectUrls.current.add(url);
        setSpeechOutput({ messageId: message.id, asset: result.asset, url });
      } catch {
        if (!controller.signal.aborted) {
          setVoiceNotice("The local response could not be synthesized.");
        }
      } finally {
        if (voiceController.current === controller) voiceController.current = null;
        setSynthesizingMessageId(null);
      }
    },
    [
      onSynthesizeVoice,
      onDeleteAttachment,
      recording,
      speechOutput,
      transcribing,
      voiceOutputAvailable,
    ],
  );

  const deleteSpeechOutput = useCallback(async () => {
    if (speechOutput === null) return;
    const current = speechOutput;
    try {
      await onDeleteAttachment(current.asset.id);
      URL.revokeObjectURL(current.url);
      objectUrls.current.delete(current.url);
      setSpeechOutput(null);
    } catch {
      setVoiceNotice("The synthesized audio could not be deleted.");
    }
  }, [onDeleteAttachment, speechOutput]);

  const submitImageGeneration = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      if (
        !imagePrompt.trim() ||
        imageOperation !== null ||
        generating ||
        recording ||
        transcribing ||
        synthesizingMessageId !== null ||
        onGenerateImage === undefined
      ) return;
      setImageOperation("generating");
      try {
        await onGenerateImage(imagePrompt);
        setImagePrompt("");
      } catch {
        // The app-level safe notice owns runtime error presentation.
      } finally {
        setImageOperation(null);
      }
    },
    [
      generating,
      imageOperation,
      imagePrompt,
      onGenerateImage,
      recording,
      synthesizingMessageId,
      transcribing,
    ],
  );

  const submitImageEdit = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      if (
        editingAttachment === null ||
        !editInstruction.trim() ||
        imageOperation !== null ||
        generating ||
        recording ||
        transcribing ||
        synthesizingMessageId !== null ||
        onEditImage === undefined
      ) return;
      setImageOperation("editing");
      try {
        await onEditImage(editingAttachment.id, editInstruction);
        setEditInstruction("");
        setEditingAttachment(null);
      } catch {
        // The app-level safe notice owns runtime error presentation.
      } finally {
        setImageOperation(null);
      }
    },
    [
      editInstruction,
      editingAttachment,
      generating,
      imageOperation,
      onEditImage,
      recording,
      synthesizingMessageId,
      transcribing,
    ],
  );

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
        onIngestDocument={onIngestDocument}
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
    setDraftSource("text");
    if (attachmentIds.length > 0) {
      await onGenerate(message, attachmentIds);
    } else {
      await onGenerate(message);
    }
  }

  async function submitMission() {
    if (
      onCreateMission === undefined ||
      missionSubmitting ||
      draft === "" ||
      draft !== draft.trim() ||
      queue.readyAssetIds.length > 0 ||
      queue.unresolved
    ) return;
    setMissionSubmitting(true);
    setVoiceNotice(null);
    try {
      await onCreateMission(draft, draftSource);
      setDraft("");
      setDraftSource("text");
      setVoiceNotice("Mission created. Live execution is available in Mission Control.");
    } catch {
      setVoiceNotice("The mission could not be created.");
    } finally {
      setMissionSubmitting(false);
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
      {voiceNotice !== null && (
        <p className="notice notice-error" role="alert">{voiceNotice}</p>
      )}

      <div className="message-region" aria-live="polite" aria-busy={loadingMessages}>
        {loadingMessages && <p className="muted" role="status">Loading history…</p>}
        {!loadingMessages && messages.length === 0 && (
          <p className="empty-copy">This conversation has no messages.</p>
        )}
        <ol className="message-list">
          {messages.map((message, messageIndex) => {
            const previousMessage = messages[messageIndex - 1];
            const canBranchTextMessage =
              onEditAndResend !== undefined &&
              message.role === "user" &&
              message.attachments.length === 0;
            const canRegenerateAssistant =
              onRegenerate !== undefined &&
              message.role === "assistant" &&
              previousMessage?.role === "user" &&
              previousMessage.attachments.length === 0;
            return (
            <li className={`message message-${message.role}`} key={message.id}>
              <div className="message-meta">
                <strong>{message.role}</strong>
                <time dateTime={message.created_at}>
                  {formatTimestamp(message.created_at)}
                </time>
              </div>
              <MessageContent content={message.content} role={message.role} />
              {canBranchTextMessage && editingMessageId !== message.id && (
                <button
                  type="button"
                  className="button button-quiet message-branch-action"
                  disabled={generating || creatingConversation}
                  onClick={() => {
                    setEditingMessageId(message.id);
                    setEditedMessageContent(message.content);
                  }}
                >
                  Edit and resend in a branch
                </button>
              )}
              {canBranchTextMessage && editingMessageId === message.id && (
                <form
                  className="message-edit-form"
                  onSubmit={(event) => {
                    event.preventDefault();
                    const normalized = editedMessageContent.trim();
                    if (normalized.length === 0) return;
                    void onEditAndResend!(message, editedMessageContent)
                      .then(() => {
                        setEditingMessageId(null);
                        setEditedMessageContent("");
                      })
                      .catch(() => undefined);
                  }}
                >
                  <label htmlFor={`edit-message-${message.id}`}>
                    Edit user message for a new immutable branch
                  </label>
                  <textarea
                    id={`edit-message-${message.id}`}
                    rows={3}
                    maxLength={100000}
                    value={editedMessageContent}
                    onChange={(event) => setEditedMessageContent(event.target.value)}
                    disabled={generating || creatingConversation}
                  />
                  <div className="composer-actions">
                    <button
                      className="button button-primary"
                      disabled={
                        generating ||
                        creatingConversation ||
                        editedMessageContent.trim().length === 0
                      }
                    >
                      Send edited branch
                    </button>
                    <button
                      type="button"
                      className="button button-quiet"
                      disabled={generating || creatingConversation}
                      onClick={() => {
                        setEditingMessageId(null);
                        setEditedMessageContent("");
                      }}
                    >
                      Cancel
                    </button>
                  </div>
                </form>
              )}
              {canRegenerateAssistant && (
                <button
                  type="button"
                  className="button button-quiet message-branch-action"
                  disabled={generating || creatingConversation || !canGenerate}
                  onClick={() => {
                    void onRegenerate!(previousMessage!).catch(() => undefined);
                  }}
                >
                  Regenerate in a branch
                </button>
              )}
              {message.role === "assistant" && voiceOutputAvailable && (
                <div className="voice-output-controls">
                  <button
                    type="button"
                    className="button button-quiet"
                    disabled={
                      synthesizingMessageId !== null || recording || transcribing
                    }
                    onClick={() => void synthesizeMessage(message)}
                  >
                    {synthesizingMessageId === message.id
                      ? "Synthesizing…"
                      : "Read aloud"}
                  </button>
                  {synthesizingMessageId === message.id && (
                    <button
                      type="button"
                      className="button button-quiet"
                      onClick={() => voiceController.current?.abort()}
                    >
                      Cancel speech
                    </button>
                  )}
                  {speechOutput?.messageId === message.id && (
                    <div className="voice-playback">
                      <audio controls src={speechOutput.url}>
                        Local synthesized audio is ready to download.
                      </audio>
                      <button
                        type="button"
                        className="button button-quiet"
                        onClick={() => void deleteSpeechOutput()}
                      >
                        Delete audio
                      </button>
                    </div>
                  )}
                </div>
              )}
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
                          {isVisionImageMediaType(attachment.media_type) &&
                            attachment.provenance_kind === "image_generation" && (
                              <figure className="generated-image">
                                <OwnedImagePreview
                                  assetId={attachment.id}
                                  label="Locally generated image"
                                  onDownload={onDownloadAttachment}
                                />
                                <figcaption>Generated locally</figcaption>
                              </figure>
                            )}
                          {isVisionImageMediaType(attachment.media_type) &&
                            attachment.provenance_kind === "image_editing" &&
                            attachment.source_asset_id !== null && (
                              <figure className="image-comparison">
                                <div>
                                  <OwnedImagePreview
                                    assetId={attachment.source_asset_id}
                                    label="Original image before local edit"
                                    onDownload={onDownloadAttachment}
                                  />
                                  <span>Original</span>
                                </div>
                                <div>
                                  <OwnedImagePreview
                                    assetId={attachment.id}
                                    label="Locally edited image"
                                    onDownload={onDownloadAttachment}
                                  />
                                  <span>Edited</span>
                                </div>
                                <figcaption>Local edit comparison</figcaption>
                              </figure>
                            )}
                          {imageEditingAvailable &&
                            onEditImage !== undefined &&
                            isVisionImageMediaType(attachment.media_type) && (
                              <button
                                type="button"
                                className="button button-quiet"
                                disabled={
                                  generating ||
                                  imageOperation !== null ||
                                  recording ||
                                  transcribing ||
                                  synthesizingMessageId !== null
                                }
                                onClick={() => {
                                  setEditingAttachment(attachment);
                                  setEditInstruction("");
                                }}
                              >
                                Edit locally
                              </button>
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
              {message.citations.length > 0 && (
                <section className="message-citations" aria-label="Sources">
                  <strong>Sources</strong>
                  <ol>
                    {message.citations.map((citation) => (
                      <li key={citation.asset_id + ":" + citation.position}>
                        {citation.state === "deleted" ? (
                          <span className="attachment-tombstone">
                            Deleted document source
                          </span>
                        ) : (
                          <>
                            <div className="citation-heading">
                              <span>{citation.original_filename ?? "Document"}</span>
                              {citationLocation(citation) && (
                                <span>{citationLocation(citation)}</span>
                              )}
                            </div>
                            {citation.excerpt !== null && (
                              <p className="citation-excerpt">{citation.excerpt}</p>
                            )}
                          </>
                        )}
                      </li>
                    ))}
                  </ol>
                </section>
              )}
            </li>
            );
          })}
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

      {(imageGenerationAvailable || editingAttachment !== null) && (
        <section className="image-studio" aria-labelledby="image-studio-title">
          <div className="image-studio-heading">
            <div>
              <p className="eyebrow">Local media</p>
              <h3 id="image-studio-title">Image studio</h3>
            </div>
            {editingAttachment !== null && (
              <button
                type="button"
                className="button button-quiet"
                disabled={imageOperation !== null}
                onClick={() => setEditingAttachment(null)}
              >
                Close edit
              </button>
            )}
          </div>
          {editingAttachment === null ? (
            imageGenerationAvailable &&
            onGenerateImage !== undefined && (
              <form onSubmit={(event) => void submitImageGeneration(event)}>
                <label htmlFor="image-prompt">Image prompt</label>
                <textarea
                  id="image-prompt"
                  rows={3}
                  maxLength={2000}
                  value={imagePrompt}
                  onChange={(event) => setImagePrompt(event.target.value)}
                  disabled={generating || imageOperation !== null}
                  placeholder="Describe an image to generate locally"
                />
                <div className="composer-actions">
                  {imageOperation === "generating" ? (
                    <>
                      <span role="status">Generating locally…</span>
                      <button
                        type="button"
                        className="button button-secondary"
                        onClick={onCancelGeneration}
                      >
                        Cancel image
                      </button>
                    </>
                  ) : (
                    <button
                      className="button button-secondary"
                      disabled={
                        generating ||
                        recording ||
                        transcribing ||
                        synthesizingMessageId !== null ||
                        !imagePrompt.trim()
                      }
                    >
                      Generate image
                    </button>
                  )}
                </div>
              </form>
            )
          ) : (
            <form onSubmit={(event) => void submitImageEdit(event)}>
              <p className="muted">
                Editing {editingAttachment.original_filename ?? "owned image"}; the
                original will be preserved.
              </p>
              <label htmlFor="image-edit-instruction">Edit instruction</label>
              <textarea
                id="image-edit-instruction"
                rows={3}
                maxLength={2000}
                value={editInstruction}
                onChange={(event) => setEditInstruction(event.target.value)}
                disabled={generating || imageOperation !== null}
                placeholder="Describe the local edit"
              />
              <div className="composer-actions">
                {imageOperation === "editing" ? (
                  <>
                    <span role="status">Editing locally…</span>
                    <button
                      type="button"
                      className="button button-secondary"
                      onClick={onCancelGeneration}
                    >
                      Cancel edit
                    </button>
                  </>
                ) : (
                  <button
                    className="button button-secondary"
                    disabled={
                      generating ||
                      recording ||
                      transcribing ||
                      synthesizingMessageId !== null ||
                      !editInstruction.trim()
                    }
                  >
                    Create edited copy
                  </button>
                )}
              </div>
            </form>
          )}
        </section>
      )}

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
          onChange={(event) => {
            setDraft(event.target.value);
            setDraftSource("text");
          }}
          disabled={generating}
        />
        <AttachmentPicker queue={queue} disabled={generating} />
        {voiceInputAvailable && (
          <div className="voice-input-controls">
            <input
              ref={voiceUploadInput}
              className="sr-only"
              type="file"
              accept=".wav,.ogg,.mp3,.webm,audio/wav,audio/ogg,audio/mpeg,audio/webm"
              aria-label="Upload audio for transcription"
              disabled={
                generating ||
                recording ||
                transcribing ||
                synthesizingMessageId !== null
              }
              onChange={uploadVoice}
            />
            {recording ? (
              <button
                type="button"
                className="button button-secondary"
                onClick={stopRecording}
              >
                Stop recording
              </button>
            ) : transcribing ? (
              <>
                <span role="status">Transcribing locally…</span>
                <button
                  type="button"
                  className="button button-secondary"
                  onClick={() => voiceController.current?.abort()}
                >
                  Cancel transcription
                </button>
              </>
            ) : (
              <>
                <button
                  type="button"
                  className="button button-secondary"
                  disabled={generating || synthesizingMessageId !== null}
                  onClick={() => void startRecording()}
                >
                  Record voice
                </button>
                <button
                  type="button"
                  className="button button-secondary"
                  disabled={generating || synthesizingMessageId !== null}
                  onClick={() => voiceUploadInput.current?.click()}
                >
                  Upload audio
                </button>
              </>
            )}
            {recording && <span role="status">Recording locally (12 seconds max)…</span>}
          </div>
        )}
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
            <>
              {onCreateMission !== undefined && (
                <button
                  type="button"
                  className="button button-secondary"
                  disabled={
                    missionSubmitting ||
                    !draft ||
                    draft !== draft.trim() ||
                    queue.readyAssetIds.length > 0 ||
                    queue.unresolved
                  }
                  onClick={() => void submitMission()}
                >
                  {missionSubmitting ? "Creating mission…" : "Run as mission"}
                </button>
              )}
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
            </>
          )}
        </div>
      </form>
    </section>
  );
}
