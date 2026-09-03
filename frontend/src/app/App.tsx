import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { resolvePresenceState, type PresenceState } from "@work-station/shared";

import { ApiClient, ApiError, type UploadProgress } from "../api/client";
import type {
  Asset,
  AgentOSCapabilities,
  AgentRun,
  AgentRunCreateRequest,
  AgentRunEvent,
  ConversationCreateRequest,
  ConversationCursor,
  ConversationSummary,
  ConversationStateUpdateRequest,
  Connector,
  ConnectorExecution,
  ConnectorExecutionRequest,
  ConnectorExecutionResult,
  ConnectorPlatform,
  ConnectorSettings,
  ConnectorWriteRequest,
  CommunicationAccepted,
  CommunicationCapabilities,
  CommunicationRequest,
  CreativeCapabilities,
  CreativeExperience,
  CreativeExperienceCreateRequest,
  CreativeTurnCreateRequest,
  CurrentUser,
  BacktestRequest,
  ExternalAISettings,
  ExternalProvider,
  ExternalProviderUpsertRequest,
  FeatureLayer,
  FeatureRegistry,
  FinanceArtifact,
  FinanceWorkspace,
  FinanceWorkspaceCreateRequest,
  IndexedDocument,
  LearningActivityCreateRequest,
  LearningAttempt,
  LearningAttemptRequest,
  LearningCapabilities,
  LearningProgram,
  LearningProgramCreateRequest,
  LearningReviewItem,
  LearningReviewItemCreateRequest,
  LearningReviewRequest,
  LocalModel,
  MarketingAnalyticsRequest,
  MarketingCampaign,
  MarketingCampaignCreateRequest,
  MarketAlert,
  MarketAlertRequest,
  MarketQuoteRequest,
  MarketResearchRequest,
  MarketWatchItemRequest,
  MemoryCreateRequest,
  MemorySetting,
  Message,
  PersonalMemory,
  PaperOrder,
  PaperOrderRequest,
  PortfolioAnalysis,
  PortfolioAnalysisRequest,
  ProductCapability,
  ProductFeature,
  SelfUpdateStatus,
  SystemDiagnostics,
  ToolDescriptor,
  ToolExecution,
  ToolExecutionRequest,
  TradingJournalRequest,
  TradingSafetyAudit,
  TradingSafetyPolicy,
  TradingSafetyPolicyConfigureRequest,
  TradingSafetyToggleRequest,
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
import { ConversationList } from "../features/conversations/ConversationList";
import { ModelSelector } from "../features/models/ModelSelector";
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
const AgentPanel = lazy(() =>
  import("../features/agents/AgentPanel").then((module) => ({
    default: module.AgentPanel,
  })),
);
const ConnectorPanel = lazy(() =>
  import("../features/connectors/ConnectorPanel").then((module) => ({
    default: module.ConnectorPanel,
  })),
);
const CommunicationPanel = lazy(() =>
  import("../features/communications/CommunicationPanel").then((module) => ({
    default: module.CommunicationPanel,
  })),
);
const CreativePanel = lazy(() =>
  import("../features/creative/CreativePanel").then((module) => ({
    default: module.CreativePanel,
  })),
);
const FinancePanel = lazy(() =>
  import("../features/finance/FinancePanel").then((module) => ({
    default: module.FinancePanel,
  })),
);
const LearningPanel = lazy(() =>
  import("../features/learning/LearningPanel").then((module) => ({
    default: module.LearningPanel,
  })),
);
const MarketingPanel = lazy(() =>
  import("../features/marketing/MarketingPanel").then((module) => ({
    default: module.MarketingPanel,
  })),
);
const MemoryPanel = lazy(() =>
  import("../features/memory/MemoryPanel").then((module) => ({
    default: module.MemoryPanel,
  })),
);
const SettingsPanel = lazy(() =>
  import("../features/settings/SettingsPanel").then((module) => ({
    default: module.SettingsPanel,
  })),
);
const ToolPanel = lazy(() =>
  import("../features/tools/ToolPanel").then((module) => ({
    default: module.ToolPanel,
  })),
);
const WorkflowPanel = lazy(() =>
  import("../features/workflows/WorkflowPanel").then((module) => ({
    default: module.WorkflowPanel,
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
  const [connectorsOpen, setConnectorsOpen] = useState(false);
  const [communicationsOpen, setCommunicationsOpen] = useState(false);
  const [marketingOpen, setMarketingOpen] = useState(false);
  const [financeOpen, setFinanceOpen] = useState(false);
  const [learningOpen, setLearningOpen] = useState(false);
  const [creativeOpen, setCreativeOpen] = useState(false);
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
    setConnectorsOpen(false);
    setCommunicationsOpen(false);
    setMarketingOpen(false);
    setFinanceOpen(false);
    setLearningOpen(false);
    setCreativeOpen(false);
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

  const loadConnectorSettings = useCallback(
    (signal?: AbortSignal): Promise<ConnectorSettings> => {
      if (client === null) {
        return Promise.reject(new ApiError("authentication", "Authentication failed."));
      }
      return client.getConnectorSettings(signal);
    },
    [client],
  );

  const loadConnectorPlatform = useCallback(
    (signal?: AbortSignal): Promise<ConnectorPlatform> => {
      if (client === null) {
        return Promise.reject(new ApiError("authentication", "Authentication failed."));
      }
      return client.getConnectorPlatform(signal);
    },
    [client],
  );

  const loadConnectors = useCallback(
    async (signal?: AbortSignal): Promise<Connector[]> => {
      if (client === null) {
        throw new ApiError("authentication", "Authentication failed.");
      }
      return (await client.listConnectors(signal)).items;
    },
    [client],
  );

  const loadCommunicationCapabilities = useCallback(
    (signal?: AbortSignal): Promise<CommunicationCapabilities> => {
      if (client === null) {
        return Promise.reject(new ApiError("authentication", "Authentication failed."));
      }
      return client.getCommunicationCapabilities(signal);
    },
    [client],
  );

  const startPhoneCall = useCallback(
    (request: CommunicationRequest, signal?: AbortSignal): Promise<CommunicationAccepted> => {
      if (client === null) {
        return Promise.reject(new ApiError("authentication", "Authentication failed."));
      }
      return client.startPhoneCall(request, signal);
    },
    [client],
  );

  const scheduleCallback = useCallback(
    (request: CommunicationRequest, signal?: AbortSignal): Promise<CommunicationAccepted> => {
      if (client === null) {
        return Promise.reject(new ApiError("authentication", "Authentication failed."));
      }
      return client.scheduleCallback(request, signal);
    },
    [client],
  );

  const loadConnectorAudit = useCallback(
    async (signal?: AbortSignal): Promise<ConnectorExecution[]> => {
      if (client === null) {
        throw new ApiError("authentication", "Authentication failed.");
      }
      return (await client.listConnectorExecutions({ limit: 50, signal })).items;
    },
    [client],
  );

  const createConnector = useCallback(
    (request: ConnectorWriteRequest): Promise<Connector> => {
      if (client === null) {
        return Promise.reject(new ApiError("authentication", "Authentication failed."));
      }
      return client.createConnector(request);
    },
    [client],
  );

  const checkConnectorHealth = useCallback(
    (connectorId: string): Promise<ConnectorExecutionResult> => {
      if (client === null) {
        return Promise.reject(new ApiError("authentication", "Authentication failed."));
      }
      return client.checkConnectorHealth(connectorId);
    },
    [client],
  );

  const discoverConnector = useCallback(
    (connectorId: string): Promise<ConnectorExecutionResult> => {
      if (client === null) {
        return Promise.reject(new ApiError("authentication", "Authentication failed."));
      }
      return client.discoverConnector(connectorId);
    },
    [client],
  );

  const disconnectConnector = useCallback(
    (connectorId: string): Promise<Connector> => {
      if (client === null) {
        return Promise.reject(new ApiError("authentication", "Authentication failed."));
      }
      return client.disconnectConnector(connectorId);
    },
    [client],
  );

  const reconnectConnector = useCallback(
    (connectorId: string): Promise<ConnectorExecutionResult> => {
      if (client === null) {
        return Promise.reject(new ApiError("authentication", "Authentication failed."));
      }
      return client.reconnectConnector(connectorId);
    },
    [client],
  );

  const executeConnector = useCallback(
    (
      connectorId: string,
      request: ConnectorExecutionRequest,
    ): Promise<ConnectorExecutionResult> => {
      if (client === null) {
        return Promise.reject(new ApiError("authentication", "Authentication failed."));
      }
      return client.executeConnector(connectorId, request);
    },
    [client],
  );

  const revokeConnector = useCallback(
    (connectorId: string): Promise<Connector> => {
      if (client === null) {
        return Promise.reject(new ApiError("authentication", "Authentication failed."));
      }
      return client.revokeConnector(connectorId);
    },
    [client],
  );

  const loadMarketingCampaigns = useCallback(
    async (signal?: AbortSignal): Promise<MarketingCampaign[]> => {
      if (client === null) throw new ApiError("authentication", "Authentication failed.");
      return (await client.listMarketingCampaigns(signal)).items;
    },
    [client],
  );

  const createMarketingCampaign = useCallback(
    (request: MarketingCampaignCreateRequest, signal?: AbortSignal) => {
      if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
      return client.createMarketingCampaign(request, signal);
    },
    [client],
  );

  const getMarketingCampaign = useCallback((id: string, signal?: AbortSignal) => {
    if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
    return client.getMarketingCampaign(id, signal);
  }, [client]);

  const startMarketingCampaign = useCallback((id: string, signal?: AbortSignal) => {
    if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
    return client.startMarketingCampaign(id, signal);
  }, [client]);

  const approveMarketingCampaign = useCallback((id: string, signal?: AbortSignal) => {
    if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
    return client.approveMarketingCampaign(id, signal);
  }, [client]);

  const submitMarketingAnalytics = useCallback((id: string, request: MarketingAnalyticsRequest, signal?: AbortSignal) => {
    if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
    return client.submitMarketingAnalytics(id, request, signal);
  }, [client]);

  const cancelMarketingCampaign = useCallback((id: string, signal?: AbortSignal) => {
    if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
    return client.cancelMarketingCampaign(id, signal);
  }, [client]);

  const loadFinanceWorkspaces = useCallback(async (signal?: AbortSignal): Promise<FinanceWorkspace[]> => {
    if (client === null) throw new ApiError("authentication", "Authentication failed.");
    return (await client.listFinanceWorkspaces(signal)).items;
  }, [client]);

  const getFinanceWorkspace = useCallback((id: string, signal?: AbortSignal) => {
    if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
    return client.getFinanceWorkspace(id, signal);
  }, [client]);

  const createFinanceWorkspace = useCallback((request: FinanceWorkspaceCreateRequest, signal?: AbortSignal) => {
    if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
    return client.createFinanceWorkspace(request, signal);
  }, [client]);

  const addMarketWatchItem = useCallback((id: string, request: MarketWatchItemRequest, signal?: AbortSignal) => {
    if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
    return client.addMarketWatchItem(id, request, signal);
  }, [client]);

  const removeMarketWatchItem = useCallback((id: string, itemId: string, signal?: AbortSignal) => {
    if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
    return client.removeMarketWatchItem(id, itemId, signal);
  }, [client]);

  const runMarketResearch = useCallback((id: string, request: MarketResearchRequest, signal?: AbortSignal): Promise<FinanceArtifact> => {
    if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
    return client.runMarketResearch(id, request, signal);
  }, [client]);

  const runMarketBacktest = useCallback((id: string, request: BacktestRequest, signal?: AbortSignal): Promise<FinanceArtifact> => {
    if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
    return client.runMarketBacktest(id, request, signal);
  }, [client]);

  const executePaperOrder = useCallback((id: string, request: PaperOrderRequest, signal?: AbortSignal): Promise<PaperOrder> => {
    if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
    return client.executePaperOrder(id, request, signal);
  }, [client]);

  const analyzePaperPortfolio = useCallback((id: string, request: PortfolioAnalysisRequest, signal?: AbortSignal): Promise<PortfolioAnalysis> => {
    if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
    return client.analyzePaperPortfolio(id, request, signal);
  }, [client]);

  const createMarketAlert = useCallback((id: string, request: MarketAlertRequest, signal?: AbortSignal): Promise<MarketAlert> => {
    if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
    return client.createMarketAlert(id, request, signal);
  }, [client]);

  const evaluateMarketAlerts = useCallback((id: string, quote: MarketQuoteRequest, signal?: AbortSignal) => {
    if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
    return client.evaluateMarketAlerts(id, quote, signal);
  }, [client]);

  const addTradingJournalEntry = useCallback((id: string, request: TradingJournalRequest, signal?: AbortSignal): Promise<FinanceArtifact> => {
    if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
    return client.addTradingJournalEntry(id, request, signal);
  }, [client]);

  const getTradingSafetyPolicy = useCallback((id: string, signal?: AbortSignal): Promise<TradingSafetyPolicy> => {
    if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
    return client.getTradingSafetyPolicy(id, signal);
  }, [client]);

  const configureTradingSafetyPolicy = useCallback((id: string, request: TradingSafetyPolicyConfigureRequest, signal?: AbortSignal): Promise<TradingSafetyPolicy> => {
    if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
    return client.configureTradingSafetyPolicy(id, request, signal);
  }, [client]);

  const setLiveTrading = useCallback((id: string, request: TradingSafetyToggleRequest, signal?: AbortSignal): Promise<TradingSafetyPolicy> => {
    if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
    return client.setLiveTrading(id, request, signal);
  }, [client]);

  const activateTradingKillSwitch = useCallback((id: string, signal?: AbortSignal): Promise<TradingSafetyPolicy> => {
    if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
    return client.activateTradingKillSwitch(id, signal);
  }, [client]);

  const getTradingSafetyAudit = useCallback((id: string, signal?: AbortSignal): Promise<TradingSafetyAudit> => {
    if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
    return client.getTradingSafetyAudit(id, signal);
  }, [client]);

  const loadLearningCapabilities = useCallback((signal?: AbortSignal): Promise<LearningCapabilities> => {
    if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
    return client.getLearningCapabilities(signal);
  }, [client]);

  const loadLearningPrograms = useCallback(async (signal?: AbortSignal): Promise<LearningProgram[]> => {
    if (client === null) throw new ApiError("authentication", "Authentication failed.");
    return (await client.listLearningPrograms(signal)).items;
  }, [client]);

  const getLearningProgram = useCallback((id: string, signal?: AbortSignal): Promise<LearningProgram> => {
    if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
    return client.getLearningProgram(id, signal);
  }, [client]);

  const createLearningProgram = useCallback((request: LearningProgramCreateRequest, signal?: AbortSignal): Promise<LearningProgram> => {
    if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
    return client.createLearningProgram(request, signal);
  }, [client]);

  const generateLearningLesson = useCallback((programId: string, lessonId: string, signal?: AbortSignal): Promise<LearningProgram> => {
    if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
    return client.generateLearningLesson(programId, lessonId, signal);
  }, [client]);

  const createLearningActivity = useCallback((programId: string, lessonId: string, request: LearningActivityCreateRequest, signal?: AbortSignal): Promise<LearningProgram> => {
    if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
    return client.createLearningActivity(programId, lessonId, request, signal);
  }, [client]);

  const submitLearningAttempt = useCallback((programId: string, activityId: string, request: LearningAttemptRequest, signal?: AbortSignal): Promise<LearningAttempt> => {
    if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
    return client.submitLearningAttempt(programId, activityId, request, signal);
  }, [client]);

  const createLearningReviewItem = useCallback((programId: string, request: LearningReviewItemCreateRequest, signal?: AbortSignal): Promise<LearningReviewItem> => {
    if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
    return client.createLearningReviewItem(programId, request, signal);
  }, [client]);

  const reviewLearningItem = useCallback((programId: string, itemId: string, request: LearningReviewRequest, signal?: AbortSignal): Promise<LearningReviewItem> => {
    if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
    return client.reviewLearningItem(programId, itemId, request, signal);
  }, [client]);

  const loadCreativeCapabilities = useCallback((signal?: AbortSignal): Promise<CreativeCapabilities> => {
    if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
    return client.getCreativeCapabilities(signal);
  }, [client]);

  const loadCreativeExperiences = useCallback(async (signal?: AbortSignal): Promise<CreativeExperience[]> => {
    if (client === null) throw new ApiError("authentication", "Authentication failed.");
    return (await client.listCreativeExperiences(signal)).items;
  }, [client]);

  const getCreativeExperience = useCallback((id: string, signal?: AbortSignal): Promise<CreativeExperience> => {
    if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
    return client.getCreativeExperience(id, signal);
  }, [client]);

  const createCreativeExperience = useCallback((request: CreativeExperienceCreateRequest, signal?: AbortSignal): Promise<CreativeExperience> => {
    if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
    return client.createCreativeExperience(request, signal);
  }, [client]);

  const addCreativeTurn = useCallback((id: string, request: CreativeTurnCreateRequest, signal?: AbortSignal): Promise<CreativeExperience> => {
    if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
    return client.addCreativeTurn(id, request, signal);
  }, [client]);

  const completeCreativeExperience = useCallback((id: string, signal?: AbortSignal): Promise<CreativeExperience> => {
    if (client === null) return Promise.reject(new ApiError("authentication", "Authentication failed."));
    return client.completeCreativeExperience(id, signal);
  }, [client]);

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

  const streamAgentRunEvents = useCallback(
    (
      runId: string,
      onEvent: (event: AgentRunEvent) => void,
      signal: AbortSignal,
      after = 0,
    ): Promise<void> => {
      if (client === null) {
        return Promise.reject(new ApiError("authentication", "Authentication failed."));
      }
      return client.streamAgentRunEvents(runId, onEvent, signal, after);
    },
    [client],
  );

  const createMissionFromPrompt = useCallback(
    async (goal: string, source: "text" | "voice"): Promise<void> => {
      await createAgentRun({
        goal,
        source,
        task: "general_chat",
        max_retries: 1,
        deadline_seconds: 180,
      });
      setAgentsOpen(true);
    },
    [createAgentRun],
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
      setConnectorsOpen(false);
      setCommunicationsOpen(false);
      setMarketingOpen(false);
      setFinanceOpen(false);
      setLearningOpen(false);
      setCreativeOpen(false);
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
          : learningOpen
            ? "universal_workspace"
            : creativeOpen
              ? "universal_workspace"
            : toolsOpen || connectorsOpen || communicationsOpen || marketingOpen || financeOpen
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
    setConnectorsOpen(false);
    setCommunicationsOpen(false);
    setMarketingOpen(false);
    setFinanceOpen(false);
    setLearningOpen(false);
    setCreativeOpen(false);
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
    } else if (feature.id === "marketing_agent" || feature.backend_capability === "marketing_campaign_service") {
      setMarketingOpen(true);
    } else if (
      feature.backend_capability === "market_intelligence_service" ||
      feature.backend_capability === "market_workspace_service"
    ) {
      setFinanceOpen(true);
    } else if (feature.backend_capability === "learning_program_service") {
      setLearningOpen(true);
    } else if (feature.backend_capability === "creative_experience_service") {
      setCreativeOpen(true);
    } else if (
      feature.backend_capability === "external_realtime_connector"
    ) {
      setCommunicationsOpen(true);
    } else if (
      feature.backend_capability === "connector_service" ||
      feature.backend_capability === "external_connector"
    ) {
      setConnectorsOpen(true);
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
              setConnectorsOpen(false);
              setCommunicationsOpen(false);
              setMarketingOpen(false);
              setFinanceOpen(false);
              setLearningOpen(false);
              setCreativeOpen(false);
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
              setConnectorsOpen(false);
              setCommunicationsOpen(false);
              setMarketingOpen(false);
              setFinanceOpen(false);
              setLearningOpen(false);
              setCreativeOpen(false);
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
              setConnectorsOpen(false);
              setCommunicationsOpen(false);
              setMarketingOpen(false);
              setFinanceOpen(false);
              setLearningOpen(false);
              setCreativeOpen(false);
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
              setConnectorsOpen(false);
              setCommunicationsOpen(false);
              setMarketingOpen(false);
              setFinanceOpen(false);
              setLearningOpen(false);
              setCreativeOpen(false);
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
              setConnectorsOpen(false);
              setCommunicationsOpen(false);
              setMarketingOpen(false);
              setFinanceOpen(false);
              setLearningOpen(false);
              setCreativeOpen(false);
              setCatalogLayer(null);
            }}
          >
            Agents
          </button>
          <button
            type="button"
            className="button button-secondary"
            aria-expanded={connectorsOpen}
            aria-controls="personal-connectors-panel"
            onClick={() => {
              setConnectorsOpen((current) => !current);
              setCommunicationsOpen(false);
              setMarketingOpen(false);
              setFinanceOpen(false);
              setLearningOpen(false);
              setCreativeOpen(false);
              setAgentsOpen(false);
              setMemoryOpen(false);
              setToolsOpen(false);
              setWorkflowsOpen(false);
              setSettingsOpen(false);
              setCatalogLayer(null);
            }}
          >
            Connections
          </button>
          <button
            type="button"
            className="button button-secondary"
            aria-expanded={marketingOpen}
            aria-controls="personal-marketing-panel"
            onClick={() => {
              const open = !marketingOpen;
              closeProductPanels();
              setMarketingOpen(open);
            }}
          >
            Marketing
          </button>
          <button
            type="button"
            className="button button-secondary"
            aria-expanded={financeOpen}
            aria-controls="personal-finance-panel"
            onClick={() => {
              const open = !financeOpen;
              closeProductPanels();
              setFinanceOpen(open);
            }}
          >
            Finance
          </button>
          <button
            type="button"
            className="button button-secondary"
            aria-expanded={learningOpen}
            aria-controls="personal-learning-panel"
            onClick={() => {
              const open = !learningOpen;
              closeProductPanels();
              setLearningOpen(open);
            }}
          >
            Learn
          </button>
          <button
            type="button"
            className="button button-secondary"
            aria-expanded={creativeOpen}
            aria-controls="personal-creative-panel"
            onClick={() => {
              const open = !creativeOpen;
              closeProductPanels();
              setCreativeOpen(open);
            }}
          >
            Create
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
          onOpenCommunications={() => {
            closeProductPanels();
            setCommunicationsOpen(true);
          }}
        />
        <Suspense fallback={<p className="muted" role="status">Loading workspace modules…</p>}>
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
              onStreamEvents={streamAgentRunEvents}
              onPresenceStateChange={setAgentPresence}
            />
          </div>
        )}
        {connectorsOpen && (
          <div id="personal-connectors-panel">
            <ConnectorPanel
              onClose={() => setConnectorsOpen(false)}
              onLoadSettings={loadConnectorSettings}
              onLoadPlatform={loadConnectorPlatform}
              onLoad={loadConnectors}
              onLoadAudit={loadConnectorAudit}
              onCreate={createConnector}
              onHealth={checkConnectorHealth}
              onDiscover={discoverConnector}
              onDisconnect={disconnectConnector}
              onReconnect={reconnectConnector}
              onExecute={executeConnector}
              onRevoke={revokeConnector}
            />
          </div>
        )}
        {communicationsOpen && (
          <div id="personal-communications-panel">
            <CommunicationPanel
              onClose={() => setCommunicationsOpen(false)}
              onConfigure={() => {
                setCommunicationsOpen(false);
                setConnectorsOpen(true);
              }}
              onLoadCapabilities={loadCommunicationCapabilities}
              onLoadConnectors={loadConnectors}
              onStartPhoneCall={startPhoneCall}
              onScheduleCallback={scheduleCallback}
            />
          </div>
        )}
        {marketingOpen && (
          <div id="personal-marketing-panel">
            <MarketingPanel
              onClose={() => setMarketingOpen(false)}
              onLoad={loadMarketingCampaigns}
              onLoadConnectors={loadConnectors}
              onCreate={createMarketingCampaign}
              onGet={getMarketingCampaign}
              onStart={startMarketingCampaign}
              onApprove={approveMarketingCampaign}
              onAnalytics={submitMarketingAnalytics}
              onCancel={cancelMarketingCampaign}
            />
          </div>
        )}
        {financeOpen && (
          <div id="personal-finance-panel">
            <FinancePanel
              onClose={() => setFinanceOpen(false)}
              onLoad={loadFinanceWorkspaces}
              onGet={getFinanceWorkspace}
              onCreate={createFinanceWorkspace}
              onAddWatch={addMarketWatchItem}
              onRemoveWatch={removeMarketWatchItem}
              onResearch={runMarketResearch}
              onBacktest={runMarketBacktest}
              onPaperOrder={executePaperOrder}
              onPortfolio={analyzePaperPortfolio}
              onCreateAlert={createMarketAlert}
              onEvaluateAlerts={evaluateMarketAlerts}
              onJournal={addTradingJournalEntry}
              onGetTradingPolicy={getTradingSafetyPolicy}
              onConfigureTradingPolicy={configureTradingSafetyPolicy}
              onSetLiveTrading={setLiveTrading}
              onKillSwitch={activateTradingKillSwitch}
              onGetTradingAudit={getTradingSafetyAudit}
            />
          </div>
        )}
        {learningOpen && (
          <div id="personal-learning-panel">
            <LearningPanel
              onClose={() => setLearningOpen(false)}
              onCapabilities={loadLearningCapabilities}
              onLoad={loadLearningPrograms}
              onGet={getLearningProgram}
              onCreate={createLearningProgram}
              onGenerateLesson={generateLearningLesson}
              onCreateActivity={createLearningActivity}
              onAttempt={submitLearningAttempt}
              onCreateReviewItem={createLearningReviewItem}
              onReview={reviewLearningItem}
            />
          </div>
        )}
        {creativeOpen && (
          <div id="personal-creative-panel">
            <CreativePanel
              onClose={() => setCreativeOpen(false)}
              onCapabilities={loadCreativeCapabilities}
              onLoad={loadCreativeExperiences}
              onGet={getCreativeExperience}
              onCreate={createCreativeExperience}
              onTurn={addCreativeTurn}
              onComplete={completeCreativeExperience}
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
            <FeatureCatalogPanel
              layer={catalogLayer}
              onClose={() => setCatalogLayer(null)}
              onLoad={loadFeatureRegistry}
              onOpen={openCatalogFeature}
            />
          </div>
        )}
        </Suspense>
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
          onCreateMission={createMissionFromPrompt}
          onPresenceStateChange={setVoicePresence}
        />
      </section>
    </main>
  );
}
