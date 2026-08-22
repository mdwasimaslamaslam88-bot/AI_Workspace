import { useCallback, useEffect, useRef, useState } from "react";

import { ApiClient, ApiError, type UploadProgress } from "../api/client";
import type {
  Asset,
  ConversationCreateRequest,
  ConversationCursor,
  ConversationSummary,
  CurrentUser,
  IndexedDocument,
  LocalModel,
  Message,
} from "../api/contracts";
import {
  mergeConversations,
  mergeMessages,
  modelSupportsVision,
  selectableTextModels,
} from "./collections";
import {
  clearModelPreference,
  clearSessionToken,
  readModelPreference,
  readSessionToken,
  writeModelPreference,
  writeSessionToken,
} from "../auth/session";
import { ConnectView } from "../features/auth/ConnectView";
import { ChatView, type SafeNotice } from "../features/chat/ChatView";
import { ConversationList } from "../features/conversations/ConversationList";
import { ModelSelector } from "../features/models/ModelSelector";

type AuthenticationStatus = "checking" | "anonymous" | "authenticated";

function safeNotice(
  error: unknown,
  reconciliation: "none" | "complete" | "uncertain" = "none",
): SafeNotice {
  const normalized =
    error instanceof ApiError
      ? error
      : new ApiError("unexpected", "The operation could not be completed.");
  let message = normalized.message;
  if (reconciliation === "complete") {
    message += " History was refreshed; review it before retrying.";
  } else if (reconciliation === "uncertain") {
    message += " History could not be confirmed; reconnect and reload before retrying.";
  }
  return {
    message,
    status: normalized.status,
    requestId: normalized.requestId,
  };
}

function conversationFromCreate(
  conversation: ConversationSummary,
): ConversationSummary {
  return {
    id: conversation.id,
    title: conversation.title,
    created_at: conversation.created_at,
    updated_at: conversation.updated_at,
  };
}

function abortableDelay(milliseconds: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Request cancelled.", "AbortError"));
      return;
    }
    let timeout = 0;
    const onAbort = () => {
      window.clearTimeout(timeout);
      reject(new DOMException("Request cancelled.", "AbortError"));
    };
    timeout = window.setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export function App() {
  const [authenticationStatus, setAuthenticationStatus] =
    useState<AuthenticationStatus>("checking");
  const [connecting, setConnecting] = useState(false);
  const [connectError, setConnectError] = useState<string | null>(null);
  const [client, setClient] = useState<ApiClient | null>(null);
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);

  const [models, setModels] = useState<LocalModel[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [selectedModelId, setSelectedModelId] = useState<string | null>(null);

  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversationCursor, setConversationCursor] =
    useState<ConversationCursor | null>(null);
  const [conversationsLoading, setConversationsLoading] = useState(false);
  const [conversationsLoadingMore, setConversationsLoadingMore] = useState(false);
  const [conversationsError, setConversationsError] = useState<string | null>(null);

  const [selectedConversation, setSelectedConversation] =
    useState<ConversationSummary | null>(null);
  const [creatingNew, setCreatingNew] = useState(false);
  const [creatingConversation, setCreatingConversation] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [messageCursor, setMessageCursor] = useState<number | null>(null);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [messagesLoadingMore, setMessagesLoadingMore] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [chatNotice, setChatNotice] = useState<SafeNotice | null>(null);

  const authAbort = useRef<AbortController | null>(null);
  const modelsAbort = useRef<AbortController | null>(null);
  const conversationsAbort = useRef<AbortController | null>(null);
  const messagesAbort = useRef<AbortController | null>(null);
  const generationAbort = useRef<AbortController | null>(null);

  const resetWorkspace = useCallback(() => {
    authAbort.current?.abort();
    modelsAbort.current?.abort();
    conversationsAbort.current?.abort();
    messagesAbort.current?.abort();
    generationAbort.current?.abort();
    clearSessionToken();
    setClient(null);
    setCurrentUser(null);
    setModels([]);
    setSelectedModelId(null);
    setConversations([]);
    setConversationCursor(null);
    setSelectedConversation(null);
    setMessages([]);
    setMessageCursor(null);
    setCreatingNew(false);
    setGenerating(false);
    setAuthenticationStatus("anonymous");
  }, []);

  const createClient = useCallback(
    (token: string) =>
      new ApiClient(token, {
        onUnauthorized: resetWorkspace,
      }),
    [resetWorkspace],
  );

  useEffect(() => {
    const token = readSessionToken();
    if (token === null) {
      setAuthenticationStatus("anonymous");
      return;
    }

    const controller = new AbortController();
    authAbort.current = controller;
    const restoredClient = createClient(token);
    void restoredClient
      .getCurrentUser(controller.signal)
      .then((user) => {
        if (controller.signal.aborted) return;
        setClient(restoredClient);
        setCurrentUser(user);
        setAuthenticationStatus("authenticated");
      })
      .catch((error: unknown) => {
        if (
          controller.signal.aborted &&
          !(error instanceof ApiError && error.kind === "authentication")
        ) {
          return;
        }
        clearSessionToken();
        setConnectError(
          error instanceof ApiError && error.kind === "authentication"
            ? "Your saved session is no longer valid."
            : "The local backend could not be reached.",
        );
        setAuthenticationStatus("anonymous");
      });

    return () => controller.abort();
  }, [createClient]);

  const connect = useCallback(
    async (token: string) => {
      setConnecting(true);
      setConnectError(null);
      const connectionClient = createClient(token);
      const controller = new AbortController();
      authAbort.current = controller;
      try {
        const user = await connectionClient.getCurrentUser(controller.signal);
        writeSessionToken(token);
        setClient(connectionClient);
        setCurrentUser(user);
        setAuthenticationStatus("authenticated");
      } catch (error) {
        if (error instanceof ApiError && error.kind === "cancelled") return;
        clearSessionToken();
        setConnectError(
          error instanceof ApiError && error.kind === "authentication"
            ? "Authentication failed."
            : "Could not connect to the local backend.",
        );
      } finally {
        setConnecting(false);
      }
    },
    [createClient],
  );

  const reloadModels = useCallback(async () => {
    if (client === null) return;
    modelsAbort.current?.abort();
    const controller = new AbortController();
    modelsAbort.current = controller;
    setModelsLoading(true);
    setModelsError(null);
    try {
      const response = await client.listModels(controller.signal);
      const selectable = selectableTextModels(response.items);
      setModels(response.items);
      setSelectedModelId((current) => {
        const preferred = current ?? readModelPreference();
        const selected =
          selectable.find((model) => model.model_id === preferred)?.model_id ??
          selectable[0]?.model_id ??
          null;
        if (selected === null) clearModelPreference();
        else writeModelPreference(selected);
        return selected;
      });
    } catch (error) {
      if (error instanceof ApiError && error.kind === "cancelled") return;
      setModelsError(safeNotice(error).message);
    } finally {
      if (!controller.signal.aborted) setModelsLoading(false);
    }
  }, [client]);

  const reloadConversations = useCallback(async () => {
    if (client === null) return;
    conversationsAbort.current?.abort();
    const controller = new AbortController();
    conversationsAbort.current = controller;
    setConversationsLoading(true);
    setConversationsError(null);
    try {
      const response = await client.listConversations({
        limit: 50,
        signal: controller.signal,
      });
      setConversations(response.items);
      setConversationCursor(response.next_cursor);
    } catch (error) {
      if (error instanceof ApiError && error.kind === "cancelled") return;
      setConversationsError(safeNotice(error).message);
    } finally {
      if (!controller.signal.aborted) setConversationsLoading(false);
    }
  }, [client]);

  useEffect(() => {
    if (authenticationStatus !== "authenticated" || client === null) return;
    void reloadModels();
    void reloadConversations();
  }, [authenticationStatus, client, reloadConversations, reloadModels]);

  const loadMoreConversations = useCallback(async () => {
    if (client === null || conversationCursor === null) return;
    setConversationsLoadingMore(true);
    setConversationsError(null);
    try {
      const response = await client.listConversations({
        limit: 50,
        cursor: conversationCursor,
      });
      setConversations((current) =>
        mergeConversations(current, response.items),
      );
      setConversationCursor(response.next_cursor);
    } catch (error) {
      setConversationsError(safeNotice(error).message);
    } finally {
      setConversationsLoadingMore(false);
    }
  }, [client, conversationCursor]);

  const selectConversation = useCallback(
    async (summary: ConversationSummary) => {
      if (client === null || generating) return;
      messagesAbort.current?.abort();
      const controller = new AbortController();
      messagesAbort.current = controller;
      setCreatingNew(false);
      setSelectedConversation(summary);
      setMessages([]);
      setMessageCursor(null);
      setMessagesLoading(true);
      setChatNotice(null);
      try {
        const [conversation, page] = await Promise.all([
          client.getConversation(summary.id, controller.signal),
          client.listMessages(summary.id, {
            limit: 100,
            signal: controller.signal,
          }),
        ]);
        setSelectedConversation(conversation);
        setMessages(page.items);
        setMessageCursor(page.next_cursor);
      } catch (error) {
        if (error instanceof ApiError && error.kind === "cancelled") return;
        setChatNotice(safeNotice(error));
      } finally {
        if (!controller.signal.aborted) setMessagesLoading(false);
      }
    },
    [client, generating],
  );

  const refreshMessageSnapshot = useCallback(
    async (conversationId: string, maximumPages = 2): Promise<boolean> => {
      if (client === null) return false;
      const refreshed: Message[] = [];
      let cursor: number | null = null;
      try {
        for (let pageNumber = 0; pageNumber < maximumPages; pageNumber += 1) {
          const page = await client.listMessages(conversationId, {
            limit: 100,
            ...(cursor === null ? {} : { cursor }),
          });
          refreshed.push(...page.items);
          cursor = page.next_cursor;
          if (cursor === null) break;
        }
        setMessages(mergeMessages([], refreshed));
        setMessageCursor(cursor);
        return true;
      } catch (error) {
        if (error instanceof ApiError && error.kind === "authentication") {
          return false;
        }
        return false;
      }
    },
    [client],
  );

  const reloadSelectedMessages = useCallback(async () => {
    if (selectedConversation === null) return;
    setMessagesLoading(true);
    setChatNotice(null);
    const refreshed = await refreshMessageSnapshot(selectedConversation.id, 1);
    if (!refreshed) {
      setChatNotice(
        safeNotice(
          new ApiError("network", "Could not refresh conversation history."),
        ),
      );
    }
    setMessagesLoading(false);
  }, [refreshMessageSnapshot, selectedConversation]);

  const loadMoreMessages = useCallback(async () => {
    if (
      client === null ||
      selectedConversation === null ||
      messageCursor === null
    ) {
      return;
    }
    setMessagesLoadingMore(true);
    try {
      const page = await client.listMessages(selectedConversation.id, {
        limit: 100,
        cursor: messageCursor,
      });
      setMessages((current) => mergeMessages(current, page.items));
      setMessageCursor(page.next_cursor);
    } catch (error) {
      setChatNotice(safeNotice(error));
    } finally {
      setMessagesLoadingMore(false);
    }
  }, [client, messageCursor, selectedConversation]);

  const runGeneration = useCallback(
    async (
      conversationId: string,
      userMessage?: string,
      attachmentIds: string[] = [],
    ) => {
      if (client === null || selectedModelId === null || generating) return;
      const controller = new AbortController();
      generationAbort.current = controller;
      setGenerating(true);
      setChatNotice(null);
      try {
        const response = await client.generateResponse(
          conversationId,
          {
            model_id: selectedModelId,
            ...(userMessage === undefined ? {} : { user_message: userMessage }),
            ...(userMessage !== undefined && attachmentIds.length > 0
              ? { attachment_ids: attachmentIds }
              : {}),
          },
          controller.signal,
        );
        setMessages((current) => mergeMessages(current, [response.message]));
        await refreshMessageSnapshot(conversationId, 2);
        await reloadConversations();
      } catch (error) {
        if (error instanceof ApiError && error.kind === "authentication") return;
        const reconciled = await refreshMessageSnapshot(conversationId, 2);
        setChatNotice(
          safeNotice(error, reconciled ? "complete" : "uncertain"),
        );
        if (error instanceof ApiError && error.status === 404) {
          await reloadModels();
        }
        await reloadConversations();
      } finally {
        generationAbort.current = null;
        setGenerating(false);
      }
    },
    [
      client,
      generating,
      refreshMessageSnapshot,
      reloadConversations,
      reloadModels,
      selectedModelId,
    ],
  );

  const createConversation = useCallback(
    async (request: ConversationCreateRequest) => {
      if (client === null || selectedModelId === null) return;
      setCreatingConversation(true);
      setChatNotice(null);
      try {
        const created = await client.createConversation(request);
        const summary = conversationFromCreate(created);
        setSelectedConversation(summary);
        setCreatingNew(false);
        setMessages([created.initial_message]);
        setMessageCursor(null);
        setConversations((current) =>
          mergeConversations([summary], current),
        );
        await reloadConversations();
        await runGeneration(created.id);
      } catch (error) {
        setChatNotice(safeNotice(error));
      } finally {
        setCreatingConversation(false);
      }
    },
    [client, reloadConversations, runGeneration, selectedModelId],
  );

  const uploadAttachment = useCallback(
    (
      file: File,
      idempotencyKey: string,
      options: { signal?: AbortSignal; onProgress?: (value: UploadProgress) => void },
    ): Promise<Asset> => {
      if (client === null) {
        return Promise.reject(
          new ApiError("authentication", "Authentication failed."),
        );
      }
      return client.uploadAsset(file, idempotencyKey, options);
    },
    [client],
  );

  const ingestDocument = useCallback(
    async (assetId: string, signal?: AbortSignal): Promise<IndexedDocument> => {
      if (client === null) {
        throw new ApiError("authentication", "Authentication failed.");
      }
      let document = await client.ingestDocument(assetId, signal);
      const deadline = Date.now() + 35_000;
      while (document.status === "pending" || document.status === "processing") {
        if (Date.now() >= deadline) {
          throw new ApiError(
            "unavailable",
            "Document indexing did not finish within its local deadline.",
          );
        }
        await abortableDelay(500, signal);
        document = await client.getDocument(document.id, signal);
      }
      if (document.status !== "ready") {
        throw new ApiError("validation", "Document could not be indexed safely.");
      }
      return document;
    },
    [client],
  );

  const downloadAttachment = useCallback(
    (assetId: string, signal?: AbortSignal): Promise<Blob> => {
      if (client === null) {
        return Promise.reject(
          new ApiError("authentication", "Authentication failed."),
        );
      }
      return client.downloadAsset(assetId, signal);
    },
    [client],
  );

  const deleteAttachment = useCallback(
    async (assetId: string, signal?: AbortSignal) => {
      if (client === null) {
        throw new ApiError("authentication", "Authentication failed.");
      }
      await client.deleteAsset(assetId, signal);
      if (selectedConversation !== null) {
        await refreshMessageSnapshot(selectedConversation.id, 2);
      }
    },
    [client, refreshMessageSnapshot, selectedConversation],
  );

  function chooseModel(modelId: string) {
    setSelectedModelId(modelId);
    writeModelPreference(modelId);
    setChatNotice(null);
  }

  const selectedModel =
    models.find((candidate) => candidate.model_id === selectedModelId) ?? null;

  if (authenticationStatus === "checking") {
    return (
      <main className="splash" aria-busy="true">
        <div className="brand-mark" aria-hidden="true">AI</div>
        <p role="status">Restoring local session…</p>
      </main>
    );
  }

  if (authenticationStatus === "anonymous" || currentUser === null) {
    return (
      <ConnectView
        connecting={connecting}
        error={connectError}
        onConnect={connect}
      />
    );
  }

  return (
    <main className="workspace-shell">
      <ConversationList
        conversations={conversations}
        userId={currentUser.id}
        selectedId={selectedConversation?.id ?? null}
        nextCursor={conversationCursor}
        loading={conversationsLoading}
        loadingMore={conversationsLoadingMore}
        error={conversationsError}
        disabled={generating || creatingConversation}
        onCreate={() => {
          setCreatingNew(true);
          setChatNotice(null);
        }}
        onSelect={(conversation) => void selectConversation(conversation)}
        onReload={() => void reloadConversations()}
        onLoadMore={() => void loadMoreConversations()}
        onLogout={resetWorkspace}
      />
      <section className="workspace-main">
        <header className="workspace-toolbar">
          <ModelSelector
            models={models}
            selectedModelId={selectedModelId}
            loading={modelsLoading}
            error={modelsError}
            disabled={generating || creatingConversation}
            onSelect={chooseModel}
            onReload={() => void reloadModels()}
          />
        </header>
        <ChatView
          conversation={selectedConversation}
          creatingNew={creatingNew}
          canGenerate={selectedModelId !== null}
          canUseVision={modelSupportsVision(selectedModel)}
          messages={messages}
          nextCursor={messageCursor}
          loadingMessages={messagesLoading}
          loadingMoreMessages={messagesLoadingMore}
          creatingConversation={creatingConversation}
          generating={generating}
          notice={chatNotice}
          onCreateConversation={createConversation}
          onCancelNew={() => setCreatingNew(false)}
          onGenerate={(message, attachmentIds) =>
            selectedConversation === null
              ? Promise.resolve()
              : runGeneration(selectedConversation.id, message, attachmentIds)
          }
          onCancelGeneration={() => generationAbort.current?.abort()}
          onLoadMoreMessages={() => void loadMoreMessages()}
          onReloadMessages={() => void reloadSelectedMessages()}
          onUploadAttachment={uploadAttachment}
          onIngestDocument={ingestDocument}
          onDownloadAttachment={downloadAttachment}
          onDeleteAttachment={deleteAttachment}
        />
      </section>
    </main>
  );
}
