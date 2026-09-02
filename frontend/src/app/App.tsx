import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { resolvePresenceState, type PresenceState } from "@work-station/shared";

import { ApiClient, ApiError, type UploadProgress } from "../api/client";
import type {
  Asset,
  AgentOSCapabilities,
  AgentRun,
  AgentRunCreateRequest,
  ConversationCreateRequest,
  ConversationCursor,
  ConversationSummary,
  ConversationStateUpdateRequest,
  CurrentUser,
  ExternalAISettings,
  ExternalProvider,
  ExternalProviderUpsertRequest,
  FeatureLayer,
  FeatureRegistry,
  IndexedDocument,
  LocalModel,
  MemoryCreateRequest,
  MemorySetting,
  Message,
  PersonalMemory,
  ProductCapability,
  ProductFeature,
  SelfUpdateStatus,
  SystemDiagnostics,
  ToolDescriptor,
  ToolExecution,
  ToolExecutionRequest,
  Workflow,
  WorkflowCreateRequest,
} from "../api/contracts";
import {
  mergeConversations,
  mergeMessages,
  firstRunnableCapabilityModel,
  modelSupportsVision,
  selectableTextModels,
} from "./collections";
import {
  clearModelPreference,
  readModelPreference,
  writeModelPreference,
} from "../auth/session";
import {
  clearPersistedSessionToken,
  readPersistedSessionToken,
  SessionPersistenceError,
  writePersistedSessionToken,
} from "../auth/persistence";
import { ConnectView, ReconnectView } from "../features/auth/ConnectView";
import { ChatView, type SafeNotice } from "../features/chat/ChatView";
import { AgentPanel } from "../features/agents/AgentPanel";
import { ConversationList } from "../features/conversations/ConversationList";
import { MemoryPanel } from "../features/memory/MemoryPanel";
import { ModelSelector } from "../features/models/ModelSelector";
import { SettingsPanel } from "../features/settings/SettingsPanel";
import { ToolPanel } from "../features/tools/ToolPanel";
import { WorkflowPanel } from "../features/workflows/WorkflowPanel";
import { PresenceHeader } from "../features/shell/PresenceHeader";
import { ProductLayerNavigation } from "../features/shell/ProductLayerNavigation";
import {
  listenForDesktopDeepLinks,
  notifyDesktopTaskFinished,
} from "../platform/desktop";
import {
  applyAppearancePreference,
  type AppearancePreference,
  readAppearancePreference,
  writeAppearancePreference,
} from "../preferences/appearance";

type AuthenticationStatus =
  | "checking"
  | "anonymous"
  | "disconnected"
  | "authenticated";

const FeatureCatalogPanel = lazy(() =>
  import("../features/catalog/FeatureCatalogPanel").then((module) => ({
    default: module.FeatureCatalogPanel,
  })),
);

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
    is_pinned: conversation.is_pinned,
    is_archived: conversation.is_archived,
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
  const [online, setOnline] = useState(
    () => typeof navigator === "undefined" || navigator.onLine,
  );
  const [appearance, setAppearance] = useState<AppearancePreference>(
    readAppearancePreference,
  );
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
  const [showArchivedConversations, setShowArchivedConversations] = useState(false);
  const [conversationSearchInput, setConversationSearchInput] = useState("");
  const [conversationSearch, setConversationSearch] = useState("");

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
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [workflowsOpen, setWorkflowsOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [agentsOpen, setAgentsOpen] = useState(false);
  const [voicePresence, setVoicePresence] = useState<PresenceState | null>(null);
  const [agentPresence, setAgentPresence] = useState<PresenceState | null>(null);
  const [catalogLayer, setCatalogLayer] = useState<
    Extract<FeatureLayer, "universal_workspace" | "apps_hub"> | null
  >(null);

  const authAbort = useRef<AbortController | null>(null);
  const modelsAbort = useRef<AbortController | null>(null);
  const conversationsAbort = useRef<AbortController | null>(null);
  const messagesAbort = useRef<AbortController | null>(null);
  const generationAbort = useRef<AbortController | null>(null);
  const authenticationStatusRef = useRef<AuthenticationStatus>("checking");
  const onlineRef = useRef(online);

  const resetWorkspace = useCallback(() => {
    authAbort.current?.abort();
    modelsAbort.current?.abort();
    conversationsAbort.current?.abort();
    messagesAbort.current?.abort();
    generationAbort.current?.abort();
    void clearPersistedSessionToken().catch(() => {
      setConnectError("Secure session storage could not be cleared.");
    });
    setClient(null);
    setCurrentUser(null);
    setModels([]);
    setSelectedModelId(null);
    setConversations([]);
    setShowArchivedConversations(false);
    setConversationSearchInput("");
    setConversationSearch("");
    setConversationCursor(null);
    setSelectedConversation(null);
    setMessages([]);
    setMessageCursor(null);
    setCreatingNew(false);
    setGenerating(false);
    setMemoryOpen(false);
    setToolsOpen(false);
    setWorkflowsOpen(false);
    setSettingsOpen(false);
    setAgentsOpen(false);
    setCatalogLayer(null);
    setAuthenticationStatus("anonymous");
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(
      () => setConversationSearch(conversationSearchInput.trim()),
      300,
    );
    return () => window.clearTimeout(timer);
  }, [conversationSearchInput]);

  const createClient = useCallback(
    (token: string) =>
      new ApiClient(token, {
        onUnauthorized: resetWorkspace,
      }),
    [resetWorkspace],
  );

  const restoreStoredSession = useCallback(async () => {
    let token: string | null;
    try {
      token = await readPersistedSessionToken();
    } catch {
      setConnectError("Secure session storage is unavailable.");
      setAuthenticationStatus("anonymous");
      return;
    }
    if (token === null) {
      setAuthenticationStatus("anonymous");
      return;
    }

    authAbort.current?.abort();
    const controller = new AbortController();
    authAbort.current = controller;
    const restoredClient = createClient(token);
    setConnecting(true);
    setConnectError(null);
    try {
      const user = await restoredClient.getCurrentUser(controller.signal);
      if (controller.signal.aborted) return;
      setClient(restoredClient);
      setCurrentUser(user);
      setAuthenticationStatus("authenticated");
    } catch (error) {
      if (
        controller.signal.aborted &&
        !(error instanceof ApiError && error.kind === "authentication")
      ) {
        return;
      }
      if (error instanceof ApiError && error.kind === "authentication") {
        await clearPersistedSessionToken().catch(() => undefined);
        setConnectError("Your saved session is no longer valid.");
        setAuthenticationStatus("anonymous");
        return;
      }
      setClient(null);
      setCurrentUser(null);
      setConnectError(
        onlineRef.current
          ? "WORK STATION could not reach the configured backend."
          : "Your network is offline. WORK STATION will keep this session ready.",
      );
      setAuthenticationStatus("disconnected");
    } finally {
      if (authAbort.current === controller) setConnecting(false);
    }
  }, [createClient]);

  useEffect(() => {
    void restoreStoredSession();
    return () => authAbort.current?.abort();
  }, [restoreStoredSession]);

  useEffect(() => {
    authenticationStatusRef.current = authenticationStatus;
  }, [authenticationStatus]);

  useEffect(() => {
    applyAppearancePreference(appearance);
    writeAppearancePreference(appearance);
    if (appearance !== "system" || typeof window.matchMedia !== "function") {
      return;
    }
    const preference = window.matchMedia("(prefers-color-scheme: dark)");
    const refresh = () => applyAppearancePreference("system");
    preference.addEventListener("change", refresh);
    return () => preference.removeEventListener("change", refresh);
  }, [appearance]);

  useEffect(() => {
    const handleOnline = () => {
      onlineRef.current = true;
      setOnline(true);
      if (authenticationStatusRef.current === "disconnected") {
        void restoreStoredSession();
      }
    };
    const handleOffline = () => {
      onlineRef.current = false;
      setOnline(false);
    };
    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);
    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
    };
  }, [restoreStoredSession]);

  const connect = useCallback(
    async (token: string) => {
      setConnecting(true);
      setConnectError(null);
      const connectionClient = createClient(token);
      const controller = new AbortController();
      authAbort.current = controller;
      try {
        const user = await connectionClient.getCurrentUser(controller.signal);
        await writePersistedSessionToken(token);
        setClient(connectionClient);
        setCurrentUser(user);
        setAuthenticationStatus("authenticated");
      } catch (error) {
        if (error instanceof ApiError && error.kind === "cancelled") return;
        await clearPersistedSessionToken().catch(() => undefined);
        setConnectError(
          error instanceof SessionPersistenceError
            ? error.message
            : error instanceof ApiError && error.kind === "authentication"
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
      const response = conversationSearch
        ? await client.searchConversations(
            {
              query: conversationSearch,
              limit: 50,
              include_archived: showArchivedConversations,
            },
            controller.signal,
          )
        : await client.listConversations({
            limit: 50,
            includeArchived: showArchivedConversations,
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
  }, [client, conversationSearch, showArchivedConversations]);

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
      const response = conversationSearch
        ? await client.searchConversations({
            query: conversationSearch,
            limit: 50,
            cursor_updated_at: conversationCursor.updated_at,
            cursor_id: conversationCursor.id,
            include_archived: showArchivedConversations,
          })
        : await client.listConversations({
            limit: 50,
            cursor: conversationCursor,
            includeArchived: showArchivedConversations,
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
  }, [
    client,
    conversationCursor,
    conversationSearch,
    showArchivedConversations,
  ]);

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

  const renameConversation = useCallback(
    async (conversationId: string, title: string | null): Promise<void> => {
      if (client === null) {
        throw new ApiError("authentication", "Authentication failed.");
      }
      const renamed = await client.renameConversation(conversationId, { title });
      setConversations((current) =>
        current.map((conversation) =>
          conversation.id === renamed.id ? renamed : conversation,
        ),
      );
      setSelectedConversation((current) =>
        current?.id === renamed.id ? renamed : current,
      );
      if (conversationSearch) await reloadConversations();
    },
    [client, conversationSearch, reloadConversations],
  );

  const deleteConversation = useCallback(
    async (conversationId: string): Promise<void> => {
      if (client === null) {
        throw new ApiError("authentication", "Authentication failed.");
      }
      await client.deleteConversation(conversationId);
      setConversations((current) =>
        current.filter((conversation) => conversation.id !== conversationId),
      );
      if (selectedConversation?.id === conversationId) {
        setSelectedConversation(null);
        setMessages([]);
        setMessageCursor(null);
        setCreatingNew(true);
        setChatNotice(null);
      }
    },
    [client, selectedConversation?.id],
  );

  const updateConversationState = useCallback(
    async (
      conversationId: string,
      state: ConversationStateUpdateRequest,
    ): Promise<void> => {
      if (client === null) {
        throw new ApiError("authentication", "Authentication failed.");
      }
      const updated = await client.updateConversationState(conversationId, state);
      const hiddenByArchive = updated.is_archived && !showArchivedConversations;
      setConversations((current) => hiddenByArchive
        ? current.filter((conversation) => conversation.id !== updated.id)
        : current.map((conversation) =>
            conversation.id === updated.id ? updated : conversation,
          ),
      );
      setSelectedConversation((current) => {
        if (current?.id !== updated.id) return current;
        return hiddenByArchive ? null : updated;
      });
      if (hiddenByArchive && selectedConversation?.id === updated.id) {
        setMessages([]);
        setMessageCursor(null);
        setCreatingNew(true);
        setChatNotice(null);
      }
    },
    [client, selectedConversation?.id, showArchivedConversations],
  );

  const duplicateConversation = useCallback(
    async (conversationId: string): Promise<void> => {
      if (client === null) {
        throw new ApiError("authentication", "Authentication failed.");
      }
      const fork = await client.forkConversation(conversationId);
      setConversations((current) => mergeConversations([fork], current));
      await reloadConversations();
      await selectConversation(fork);
    },
    [client, reloadConversations, selectConversation],
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

  const branchAndGenerate = useCallback(
    async (userMessage: Message, replacementContent?: string): Promise<void> => {
      if (client === null || selectedModelId === null || generating) return;
      setCreatingConversation(true);
      setChatNotice(null);
      let fork: ConversationSummary;
      try {
        const createdFork = await client.forkConversation(
          userMessage.conversation_id,
          {
            through_sequence_number: userMessage.sequence_number,
            ...(replacementContent === undefined
              ? {}
              : { replacement_content: replacementContent }),
          },
        );
        fork = createdFork;
        const page = await client.listMessages(createdFork.id, { limit: 100 });
        setSelectedConversation(createdFork);
        setCreatingNew(false);
        setMessages(page.items);
        setMessageCursor(page.next_cursor);
        setConversations((current) =>
          mergeConversations([createdFork], current),
        );
        await reloadConversations();
      } catch (error) {
        setChatNotice(safeNotice(error));
        throw error;
      } finally {
        setCreatingConversation(false);
      }
      await runGeneration(fork.id);
    },
    [client, generating, reloadConversations, runGeneration, selectedModelId],
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

  const loadMemory = useCallback(
    async (signal?: AbortSignal): Promise<{
      memories: PersonalMemory[];
      setting: MemorySetting;
    }> => {
      if (client === null) {
        throw new ApiError("authentication", "Authentication failed.");
      }
      const [page, setting] = await Promise.all([
        client.listMemories({ includeDeleted: true, signal }),
        client.getMemorySetting(signal),
      ]);
      return { memories: page.items, setting };
    },
    [client],
  );

  const loadProductCapabilities = useCallback(
    async (signal?: AbortSignal): Promise<ProductCapability[]> => {
      if (client === null) {
        throw new ApiError("authentication", "Authentication failed.");
      }
      return (await client.getProductCapabilities(signal)).items;
    },
    [client],
  );

  const loadFeatureRegistry = useCallback(
    (signal?: AbortSignal): Promise<FeatureRegistry> => {
      if (client === null) {
        return Promise.reject(new ApiError("authentication", "Authentication failed."));
      }
      return client.getFeatureRegistry(signal);
    },
    [client],
  );

  const loadSystemDiagnostics = useCallback(
    async (signal?: AbortSignal): Promise<SystemDiagnostics> => {
      if (client === null) {
        throw new ApiError("authentication", "Authentication failed.");
      }
      return client.getSystemDiagnostics(signal);
    },
    [client],
  );

  const loadExternalAISettings = useCallback(
    async (signal?: AbortSignal): Promise<ExternalAISettings> => {
      if (client === null) {
        throw new ApiError("authentication", "Authentication failed.");
      }
      return client.getExternalAISettings(signal);
    },
    [client],
  );

  const setExternalAIEnabled = useCallback(
    (enabled: boolean): Promise<ExternalAISettings> => {
      if (client === null) {
        return Promise.reject(new ApiError("authentication", "Authentication failed."));
      }
      return client.updateExternalAIEnabled(enabled);
    },
    [client],
  );

  const upsertExternalAIProvider = useCallback(
    (
      providerId: string,
      provider: ExternalProviderUpsertRequest,
    ): Promise<ExternalAISettings> => {
      if (client === null) {
        return Promise.reject(new ApiError("authentication", "Authentication failed."));
      }
      return client.upsertExternalAIProvider(providerId, provider);
    },
    [client],
  );

  const setExternalAIProviderEnabled = useCallback(
    (provider: ExternalProvider, enabled: boolean): Promise<ExternalAISettings> => {
      const models = provider.models.map((model) => ({
        model_id: model.model_id,
        tasks: model.tasks,
        verified: model.verified,
        ...(model.verification_evidence_sha256 === null
          ? {}
          : { verification_evidence_sha256: model.verification_evidence_sha256 }),
        measured_quality: model.measured_quality,
        measured_latency_ms: model.measured_latency_ms,
        stability_rate: model.stability_rate,
        context_window: model.context_window,
        input_cost_micros_per_million_tokens: model.input_cost_micros_per_million_tokens,
        output_cost_micros_per_million_tokens: model.output_cost_micros_per_million_tokens,
      }));
      return upsertExternalAIProvider(provider.provider_id, {
        kind: provider.kind,
        enabled,
        free_tier: provider.free_tier,
        priority: provider.priority,
        timeout_seconds: provider.timeout_seconds,
        rate_limit_requests_per_minute: provider.rate_limit_requests_per_minute,
        spending_limit_micros: provider.spending_limit_micros,
        quota_remaining_tokens: provider.quota_remaining_tokens,
        models,
      });
    },
    [upsertExternalAIProvider],
  );

  const deleteExternalAIProvider = useCallback(
    (providerId: string): Promise<ExternalAISettings> => {
      if (client === null) {
        return Promise.reject(new ApiError("authentication", "Authentication failed."));
      }
      return client.deleteExternalAIProvider(providerId);
    },
    [client],
  );

  const loadSelfUpdateStatus = useCallback(
    (signal?: AbortSignal): Promise<SelfUpdateStatus> => {
      if (client === null) {
        return Promise.reject(new ApiError("authentication", "Authentication failed."));
      }
      return client.getSelfUpdateStatus(signal);
    },
    [client],
  );

  const loadAgentCapabilities = useCallback(
    (signal?: AbortSignal): Promise<AgentOSCapabilities> => {
      if (client === null) {
        return Promise.reject(new ApiError("authentication", "Authentication failed."));
      }
      return client.getAgentOSCapabilities(signal);
    },
    [client],
  );

  const loadAgentRuns = useCallback(
    async (signal?: AbortSignal): Promise<AgentRun[]> => {
      if (client === null) {
        throw new ApiError("authentication", "Authentication failed.");
      }
      return (await client.listAgentRuns(signal)).items;
    },
    [client],
  );

  const createAgentRun = useCallback(
    (request: AgentRunCreateRequest): Promise<AgentRun> => {
      if (client === null) {
        return Promise.reject(new ApiError("authentication", "Authentication failed."));
      }
      return client.createAgentRun(request);
    },
    [client],
  );

  const cancelAgentRun = useCallback(
    (runId: string): Promise<AgentRun> => {
      if (client === null) {
        return Promise.reject(new ApiError("authentication", "Authentication failed."));
      }
      return client.cancelAgentRun(runId);
    },
    [client],
  );

  const decideSelfUpdate = useCallback(
    (decision: "update" | "cancel"): Promise<SelfUpdateStatus> => {
      if (client === null) {
        return Promise.reject(new ApiError("authentication", "Authentication failed."));
      }
      return client.decideSelfUpdate(decision);
    },
    [client],
  );

  const rotateSession = useCallback(
    async (signal?: AbortSignal): Promise<void> => {
      if (client === null) {
        throw new ApiError("authentication", "Authentication failed.");
      }
      const rotated = await client.rotateAccessToken(signal);
      const replacement = createClient(rotated.access_token);
      setClient(replacement);
      await writePersistedSessionToken(rotated.access_token);
    },
    [client, createClient],
  );

  const loadUserSessions = useCallback(
    async (signal?: AbortSignal) => {
      if (client === null) {
        throw new ApiError("authentication", "Authentication failed.");
      }
      return (await client.listUserSessions(signal)).items;
    },
    [client],
  );

  const createUserSession = useCallback(
    (label: string | null) => {
      if (client === null) {
        return Promise.reject(
          new ApiError("authentication", "Authentication failed."),
        );
      }
      return client.createUserSession({ label });
    },
    [client],
  );

  const renameCurrentUserSession = useCallback(
    (label: string | null) => {
      if (client === null) {
        return Promise.reject(
          new ApiError("authentication", "Authentication failed."),
        );
      }
      return client.renameCurrentUserSession({ label });
    },
    [client],
  );

  const revokeUserSession = useCallback(
    (sessionId: string) => {
      if (client === null) {
        return Promise.reject(
          new ApiError("authentication", "Authentication failed."),
        );
      }
      return client.revokeUserSession(sessionId);
    },
    [client],
  );

  const logoutCurrentSession = useCallback(async (): Promise<void> => {
    const currentClient = client;
    try {
      await currentClient?.revokeCurrentUserSession();
    } catch {
      // Local logout must still succeed if the backend is unavailable.
    } finally {
      resetWorkspace();
    }
  }, [client, resetWorkspace]);

  const createMemory = useCallback(
    (request: MemoryCreateRequest, signal?: AbortSignal): Promise<PersonalMemory> => {
      if (client === null) {
        return Promise.reject(
          new ApiError("authentication", "Authentication failed."),
        );
      }
      return client.createMemory(request, signal);
    },
    [client],
  );

  const forgetMemory = useCallback(
    (memoryId: string, signal?: AbortSignal): Promise<PersonalMemory> => {
      if (client === null) {
        return Promise.reject(
          new ApiError("authentication", "Authentication failed."),
        );
      }
      return client.forgetMemory(memoryId, signal);
    },
    [client],
  );

  const setMemoryEnabled = useCallback(
    (enabled: boolean, signal?: AbortSignal): Promise<MemorySetting> => {
      if (client === null) {
        return Promise.reject(
          new ApiError("authentication", "Authentication failed."),
        );
      }
      return client.updateMemorySetting(enabled, signal);
    },
    [client],
  );


  const loadTools = useCallback(
    async (signal?: AbortSignal): Promise<{
      tools: ToolDescriptor[];
      executions: ToolExecution[];
    }> => {
      if (client === null) {
        throw new ApiError("authentication", "Authentication failed.");
      }
      const [tools, executions] = await Promise.all([
        client.listTools(signal),
        client.listToolExecutions({ limit: 20, signal }),
      ]);
      return { tools: tools.items, executions: executions.items };
    },
    [client],
  );

  const executeTool = useCallback(
    (
      toolName: string,
      request: ToolExecutionRequest,
      signal?: AbortSignal,
    ): Promise<ToolExecution> => {
      if (client === null) {
        return Promise.reject(
          new ApiError("authentication", "Authentication failed."),
        );
      }
      return client.executeTool(toolName, request, signal);
    },
    [client],
  );


  const loadWorkflows = useCallback(
    async (signal?: AbortSignal): Promise<Workflow[]> => {
      if (client === null) {
        throw new ApiError("authentication", "Authentication failed.");
      }
      return (await client.listWorkflows({ limit: 20, signal })).items;
    },
    [client],
  );

  const createWorkflow = useCallback(
    (request: WorkflowCreateRequest, signal?: AbortSignal): Promise<Workflow> => {
      if (client === null) {
        return Promise.reject(
          new ApiError("authentication", "Authentication failed."),
        );
      }
      return client.createWorkflow(request, signal);
    },
    [client],
  );

  const startWorkflow = useCallback(
    async (workflowId: string, signal?: AbortSignal): Promise<Workflow> => {
      if (client === null) {
        throw new ApiError("authentication", "Authentication failed.");
      }
      try {
        return await client.startWorkflow(workflowId, signal);
      } catch (error) {
        if (!(error instanceof ApiError && error.kind === "cancelled")) {
          void notifyDesktopTaskFinished(false).catch(() => undefined);
        }
        throw error;
      }
    },
    [client],
  );

  const getWorkflow = useCallback(
    async (workflowId: string, signal?: AbortSignal): Promise<Workflow> => {
      if (client === null) {
        throw new ApiError("authentication", "Authentication failed.");
      }
      const workflow = await client.getWorkflow(workflowId, signal);
      if (["completed", "failed", "timed_out"].includes(workflow.status)) {
        void notifyDesktopTaskFinished(workflow.status === "completed").catch(
          () => undefined,
        );
      }
      return workflow;
    },
    [client],
  );

  const cancelWorkflow = useCallback(
    (workflowId: string, signal?: AbortSignal): Promise<Workflow> => {
      if (client === null) {
        return Promise.reject(
          new ApiError("authentication", "Authentication failed."),
        );
      }
      return client.cancelWorkflow(workflowId, signal);
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

  const transcribeVoice = useCallback(
    async (recording: Blob, signal?: AbortSignal): Promise<string> => {
      if (client === null) {
        throw new ApiError("authentication", "Authentication failed.");
      }
      const model = firstRunnableCapabilityModel(models, "speech_recognition");
      if (model === null) {
        throw new ApiError("unavailable", "Local speech recognition is unavailable.");
      }
      const mediaType = recording.type.split(";", 1)[0] || "audio/webm";
      const extension =
        ({
          "audio/wav": "wav",
          "audio/x-wav": "wav",
          "audio/ogg": "ogg",
          "audio/mpeg": "mp3",
          "audio/mp3": "mp3",
          "audio/webm": "webm",
        } as Record<string, string>)[mediaType] ?? "audio";
      const idempotencyKey = crypto.randomUUID();
      const file = new File([recording], `local-recording.${extension}`, {
        type: mediaType,
      });
      const asset = await client.uploadAsset(file, idempotencyKey, { signal });
      try {
        return (
          await client.transcribeVoice(
            { asset_id: asset.id, model_id: model.model_id },
            signal,
          )
        ).text;
      } finally {
        try {
          await client.deleteAsset(asset.id);
        } catch {
          // The asset remains owner-scoped if cleanup fails; never
          // replace a valid transcript or cancellation with cleanup details.
        }
      }
    },
    [client, models],
  );

  const synthesizeVoice = useCallback(
    async (
      text: string,
      signal?: AbortSignal,
    ): Promise<{ asset: Asset; audio: Blob }> => {
      if (client === null) {
        throw new ApiError("authentication", "Authentication failed.");
      }
      const model = firstRunnableCapabilityModel(models, "speech_synthesis");
      if (model === null) {
        throw new ApiError("unavailable", "Local speech synthesis is unavailable.");
      }
      const synthesis = await client.synthesizeVoice(
        { model_id: model.model_id, text },
        crypto.randomUUID(),
        signal,
      );
      try {
        const audio = await client.downloadAsset(synthesis.asset.id, signal);
        return { asset: synthesis.asset, audio };
      } catch (error) {
        try {
          await client.deleteAsset(synthesis.asset.id);
        } catch {
          // The failed output remains owner-scoped if cleanup also fails.
        }
        throw error;
      }
    },
    [client, models],
  );

  const generateImage = useCallback(
    async (prompt: string): Promise<void> => {
      if (client === null || selectedConversation === null || generating) return;
      const model = firstRunnableCapabilityModel(models, "image_generation");
      if (model === null) {
        throw new ApiError("unavailable", "Local image generation is unavailable.");
      }
      const controller = new AbortController();
      generationAbort.current = controller;
      setGenerating(true);
      setChatNotice(null);
      try {
        const seed = crypto.getRandomValues(new Uint32Array(1))[0] ?? 0;
        const result = await client.generateImage(
          {
            conversation_id: selectedConversation.id,
            model_id: model.model_id,
            prompt,
            width: 768,
            height: 768,
            steps: 20,
            guidance: 7,
            seed,
          },
          crypto.randomUUID(),
          controller.signal,
        );
        void notifyDesktopTaskFinished(true).catch(() => undefined);
        setMessages((current) => mergeMessages(current, [result.message]));
        await refreshMessageSnapshot(selectedConversation.id, 2);
        await reloadConversations();
      } catch (error) {
        if (!(error instanceof ApiError && error.kind === "cancelled")) {
          void notifyDesktopTaskFinished(false).catch(() => undefined);
        }
        const reconciled = await refreshMessageSnapshot(
          selectedConversation.id,
          2,
        );
        await reloadConversations();
        if (!(error instanceof ApiError && error.kind === "cancelled")) {
          setChatNotice(
            safeNotice(error, reconciled ? "complete" : "uncertain"),
          );
        }
        throw error;
      } finally {
        if (generationAbort.current === controller) generationAbort.current = null;
        setGenerating(false);
      }
    },
    [
      client,
      generating,
      models,
      refreshMessageSnapshot,
      reloadConversations,
      selectedConversation,
    ],
  );

  const editImage = useCallback(
    async (sourceAssetId: string, instruction: string): Promise<void> => {
      if (client === null || selectedConversation === null || generating) return;
      const model = firstRunnableCapabilityModel(models, "image_editing");
      if (model === null) {
        throw new ApiError("unavailable", "Local image editing is unavailable.");
      }
      const controller = new AbortController();
      generationAbort.current = controller;
      setGenerating(true);
      setChatNotice(null);
      try {
        const seed = crypto.getRandomValues(new Uint32Array(1))[0] ?? 0;
        const result = await client.editImage(
          {
            conversation_id: selectedConversation.id,
            model_id: model.model_id,
            source_asset_id: sourceAssetId,
            instruction,
            steps: 20,
            guidance: 7,
            denoise: 0.65,
            seed,
          },
          crypto.randomUUID(),
          controller.signal,
        );
        void notifyDesktopTaskFinished(true).catch(() => undefined);
        setMessages((current) => mergeMessages(current, [result.message]));
        await refreshMessageSnapshot(selectedConversation.id, 2);
        await reloadConversations();
      } catch (error) {
        if (!(error instanceof ApiError && error.kind === "cancelled")) {
          void notifyDesktopTaskFinished(false).catch(() => undefined);
        }
        const reconciled = await refreshMessageSnapshot(
          selectedConversation.id,
          2,
        );
        await reloadConversations();
        if (!(error instanceof ApiError && error.kind === "cancelled")) {
          setChatNotice(
            safeNotice(error, reconciled ? "complete" : "uncertain"),
          );
        }
        throw error;
      } finally {
        if (generationAbort.current === controller) generationAbort.current = null;
        setGenerating(false);
      }
    },
    [
      client,
      generating,
      models,
      refreshMessageSnapshot,
      reloadConversations,
      selectedConversation,
    ],
  );

  function chooseModel(modelId: string) {
    setSelectedModelId(modelId);
    writeModelPreference(modelId);
    setChatNotice(null);
  }

  const selectedModel =
    models.find((candidate) => candidate.model_id === selectedModelId) ?? null;

  useEffect(() => {
    if (authenticationStatus !== "authenticated") return;
    let active = true;
    let unlisten: (() => void) | undefined;
    void listenForDesktopDeepLinks((target) => {
      if (!active) return;
      setSettingsOpen(target === "settings");
      setMemoryOpen(target === "memory");
      setToolsOpen(target === "tools" || target === "studio");
      setWorkflowsOpen(target === "workflows");
      setAgentsOpen(false);
      setCatalogLayer(null);
    })
      .then((dispose) => {
        if (active) unlisten = dispose;
        else dispose();
      })
      .catch(() => undefined);
    return () => {
      active = false;
      unlisten?.();
    };
  }, [authenticationStatus]);

  const activeLayer: FeatureLayer =
    catalogLayer ??
    (settingsOpen
      ? "ai_command_center"
      : agentsOpen || workflowsOpen
        ? "mission_control"
        : memoryOpen
          ? "universal_workspace"
          : toolsOpen
            ? "apps_hub"
            : "ai_presence");

  const presenceState = resolvePresenceState({
    voice: voicePresence ?? agentPresence,
    generating: generating || creatingConversation,
    working: messagesLoading || modelsLoading,
    needsInput: chatNotice !== null,
  });

  function closeProductPanels() {
    setSettingsOpen(false);
    setWorkflowsOpen(false);
    setToolsOpen(false);
    setMemoryOpen(false);
    setAgentsOpen(false);
    setCatalogLayer(null);
  }

  function selectProductLayer(layer: FeatureLayer) {
    closeProductPanels();
    if (layer === "mission_control") setAgentsOpen(true);
    else if (layer === "universal_workspace" || layer === "apps_hub") setCatalogLayer(layer);
    else if (layer === "ai_command_center") setSettingsOpen(true);
  }

  function openCatalogFeature(feature: ProductFeature) {
    closeProductPanels();
    if (feature.backend_capability === "bounded_tools") {
      setToolsOpen(true);
    } else if (feature.id.includes("memory") || feature.id.includes("knowledge_rag")) {
      setMemoryOpen(true);
    } else if (
      feature.backend_capability.includes("agent") ||
      feature.backend_capability === "conversation_and_agent_workspace" ||
      feature.backend_capability === "research_and_analysis_agent"
    ) {
      setAgentsOpen(true);
    } else {
      window.setTimeout(() => document.getElementById("chat-prompt")?.focus(), 0);
    }
  }

  if (authenticationStatus === "checking") {
    return (
      <main className="splash" aria-busy="true">
        <img className="brand-icon" src="/icons/icon-192.png" alt="" />
        <p role="status">Restoring local session…</p>
      </main>
    );
  }

  if (authenticationStatus === "disconnected") {
    return (
      <ReconnectView
        online={online}
        reconnecting={connecting}
        error={connectError ?? "The backend is temporarily unavailable."}
        onRetry={() => void restoreStoredSession()}
        onUseDifferentToken={resetWorkspace}
      />
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
        showArchived={showArchivedConversations}
        searchQuery={conversationSearchInput}
        disabled={generating || creatingConversation}
        onCreate={() => {
          setCreatingNew(true);
          setChatNotice(null);
        }}
        onSelect={(conversation) => void selectConversation(conversation)}
        onRename={renameConversation}
        onUpdateState={updateConversationState}
        onDuplicate={duplicateConversation}
        onDelete={deleteConversation}
        onShowArchivedChange={setShowArchivedConversations}
        onSearchQueryChange={setConversationSearchInput}
        onReload={() => void reloadConversations()}
        onLoadMore={() => void loadMoreConversations()}
        onLogout={() => void logoutCurrentSession()}
      />
      <section className="workspace-main">
        <header className="workspace-toolbar">
          <span
            className="connection-status connection-online"
            aria-label="Connection status"
            aria-live="polite"
          >
            <span aria-hidden="true" />
            Connected
          </span>
          <button
            type="button"
            className="button button-secondary"
            aria-expanded={settingsOpen}
            aria-controls="personal-settings-panel"
            onClick={() => {
              setSettingsOpen((current) => !current);
              setWorkflowsOpen(false);
              setToolsOpen(false);
              setMemoryOpen(false);
              setAgentsOpen(false);
              setCatalogLayer(null);
            }}
          >
            Settings
          </button>
          <button
            type="button"
            className="button button-secondary"
            aria-expanded={workflowsOpen}
            aria-controls="personal-workflows-panel"
            onClick={() => {
              setWorkflowsOpen((current) => !current);
              setToolsOpen(false);
              setMemoryOpen(false);
              setSettingsOpen(false);
              setAgentsOpen(false);
              setCatalogLayer(null);
            }}
          >
            Workflows
          </button>
          <button
            type="button"
            className="button button-secondary"
            aria-expanded={toolsOpen}
            aria-controls="personal-tools-panel"
            onClick={() => {
              setToolsOpen((current) => !current);
              setMemoryOpen(false);
              setWorkflowsOpen(false);
              setSettingsOpen(false);
              setAgentsOpen(false);
              setCatalogLayer(null);
            }}
          >
            Tools
          </button>
          <button
            type="button"
            className="button button-secondary"
            aria-expanded={memoryOpen}
            aria-controls="personal-memory-panel"
            onClick={() => {
              setMemoryOpen((current) => !current);
              setToolsOpen(false);
              setWorkflowsOpen(false);
              setSettingsOpen(false);
              setAgentsOpen(false);
              setCatalogLayer(null);
            }}
          >
            Memory
          </button>
          <button
            type="button"
            className="button button-secondary"
            aria-expanded={agentsOpen}
            aria-controls="personal-agents-panel"
            onClick={() => {
              setAgentsOpen((current) => !current);
              setMemoryOpen(false);
              setToolsOpen(false);
              setWorkflowsOpen(false);
              setSettingsOpen(false);
              setCatalogLayer(null);
            }}
          >
            Agents
          </button>
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
        <ProductLayerNavigation activeLayer={activeLayer} onSelect={selectProductLayer} />
        <PresenceHeader
          state={presenceState}
          modelName={selectedModel?.display_name ?? null}
          onAsk={() => {
            closeProductPanels();
            window.setTimeout(() => document.getElementById("chat-prompt")?.focus(), 0);
          }}
        />
        {settingsOpen && (
          <div id="personal-settings-panel">
            <SettingsPanel
              onClose={() => setSettingsOpen(false)}
              onLoad={loadProductCapabilities}
              onLoadDiagnostics={loadSystemDiagnostics}
              onLoadExternalAI={loadExternalAISettings}
              onSetExternalAIEnabled={setExternalAIEnabled}
              onUpsertExternalAIProvider={upsertExternalAIProvider}
              onSetExternalAIProviderEnabled={setExternalAIProviderEnabled}
              onDeleteExternalAIProvider={deleteExternalAIProvider}
              onLoadSelfUpdate={loadSelfUpdateStatus}
              onDecideSelfUpdate={decideSelfUpdate}
              onLoadSessions={loadUserSessions}
              appearance={appearance}
              onAppearanceChange={setAppearance}
              onRotateSession={rotateSession}
              onCreateSession={createUserSession}
              onRenameCurrentSession={renameCurrentUserSession}
              onRevokeSession={revokeUserSession}
              onLogout={logoutCurrentSession}
              onManageMemory={() => {
                setSettingsOpen(false);
                setMemoryOpen(true);
              }}
            />
          </div>
        )}
        {agentsOpen && (
          <div id="personal-agents-panel">
            <AgentPanel
              onClose={() => setAgentsOpen(false)}
              onLoadCapabilities={loadAgentCapabilities}
              onLoadRuns={loadAgentRuns}
              onCreate={createAgentRun}
              onCancel={cancelAgentRun}
              onPresenceStateChange={setAgentPresence}
            />
          </div>
        )}
        {workflowsOpen && (
          <div id="personal-workflows-panel">
            <WorkflowPanel
              onClose={() => setWorkflowsOpen(false)}
              onLoad={loadWorkflows}
              onCreate={createWorkflow}
              onStart={startWorkflow}
              onGet={getWorkflow}
              onCancel={cancelWorkflow}
            />
          </div>
        )}
        {toolsOpen && (
          <div id="personal-tools-panel">
            <ToolPanel
              activeConversationId={selectedConversation?.id ?? null}
              onClose={() => setToolsOpen(false)}
              onLoad={loadTools}
              onExecute={executeTool}
            />
          </div>
        )}
        {memoryOpen && (
          <div id="personal-memory-panel">
            <MemoryPanel
              onClose={() => setMemoryOpen(false)}
              onLoad={loadMemory}
              onCreate={createMemory}
              onForget={forgetMemory}
              onSetEnabled={setMemoryEnabled}
            />
          </div>
        )}
        {catalogLayer !== null && (
          <div id="feature-catalog-layer">
            <Suspense fallback={<p className="muted" role="status">Loading workspace modules…</p>}>
              <FeatureCatalogPanel
                layer={catalogLayer}
                onClose={() => setCatalogLayer(null)}
                onLoad={loadFeatureRegistry}
                onOpen={openCatalogFeature}
              />
            </Suspense>
          </div>
        )}
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
          onEditAndResend={(message, content) =>
            branchAndGenerate(message, content)
          }
          onRegenerate={(message) => branchAndGenerate(message)}
          onCancelGeneration={() => generationAbort.current?.abort()}
          onLoadMoreMessages={() => void loadMoreMessages()}
          onReloadMessages={() => void reloadSelectedMessages()}
          onUploadAttachment={uploadAttachment}
          onIngestDocument={ingestDocument}
          onDownloadAttachment={downloadAttachment}
          onDeleteAttachment={deleteAttachment}
          voiceInputAvailable={
            firstRunnableCapabilityModel(models, "speech_recognition") !== null
          }
          voiceOutputAvailable={
            firstRunnableCapabilityModel(models, "speech_synthesis") !== null
          }
          imageGenerationAvailable={
            firstRunnableCapabilityModel(models, "image_generation") !== null
          }
          imageEditingAvailable={
            firstRunnableCapabilityModel(models, "image_editing") !== null
          }
          onTranscribeVoice={transcribeVoice}
          onSynthesizeVoice={synthesizeVoice}
          onGenerateImage={generateImage}
          onEditImage={editImage}
          onPresenceStateChange={setVoicePresence}
        />
      </section>
    </main>
  );
}
