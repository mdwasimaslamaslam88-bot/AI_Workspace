import type { Asset, ConversationCursor, ConversationStateUpdateRequest, ConversationSummary, LocalModel, Message } from "@work-station/shared";
import {
  AudioModule,
  RecordingPresets,
  setAudioModeAsync,
  useAudioRecorder,
  useAudioRecorderState,
} from "expo-audio";
import * as DocumentPicker from "expo-document-picker";
import * as ImagePicker from "expo-image-picker";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Image,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  useColorScheme,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { MobileApiError, type MobileApiClient, type MobileUpload } from "@/api/client";
import { citationLocation, citationSourceLabel } from "@/chat/citations";
import { useWorkStation } from "@/context/work-station";
import { workStationColors, type WorkStationColors } from "@/theme/colors";

function safeError(cause: unknown): string {
  return cause instanceof MobileApiError
    ? cause.message
    : "The private operation could not be completed.";
}

function modelCanChat(model: LocalModel): boolean {
  return model.runnable_now && model.capabilities.includes("text_generation");
}

function mergeConversationSummaries(
  current: ConversationSummary[],
  incoming: ConversationSummary[],
): ConversationSummary[] {
  const merged = new Map(current.map((conversation) => [conversation.id, conversation]));
  for (const conversation of incoming) merged.set(conversation.id, conversation);
  return [...merged.values()];
}

function mergeMessages(current: Message[], incoming: Message[]): Message[] {
  const merged = new Map(current.map((message) => [message.id, message]));
  for (const message of incoming) merged.set(message.id, message);
  return [...merged.values()].sort(
    (left, right) => left.sequence_number - right.sequence_number,
  );
}

function useThemedStyles() {
  const scheme = useColorScheme();
  return useMemo(() => {
    const colors = workStationColors(scheme);
    return { colors, styles: createStyles(colors) };
  }, [scheme]);
}

function ConnectScreen() {
  const { colors, styles } = useThemedStyles();
  const { state, error, connect, retry, logout } = useWorkStation();
  const [token, setToken] = useState("");
  const disconnected = state === "offline" || state === "backend_unavailable";

  if (state === "connecting") {
    return (
      <View style={styles.centered} accessibilityLiveRegion="polite">
        <Image source={require("../../assets/work-station/app-icon.png")} style={styles.logo} />
        <ActivityIndicator color={colors.accent} size="large" />
        <Text style={styles.muted}>Restoring your private session…</Text>
      </View>
    );
  }

  if (disconnected) {
    return (
      <View style={styles.centered}>
        <Image source={require("../../assets/work-station/app-icon.png")} style={styles.logo} />
        <Text accessibilityRole="header" style={styles.title}>
          {state === "offline" ? "Offline" : "Backend unavailable"}
        </Text>
        <Text style={styles.muted}>{error}</Text>
        <Text style={styles.preserved}>Your secure device session is preserved.</Text>
        <Pressable accessibilityRole="button" style={styles.primaryButton} onPress={() => void retry()}>
          <Text style={styles.primaryButtonText}>Retry connection</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => void logout()}>
          <Text style={styles.buttonText}>Use a different token</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <KeyboardAvoidingView
      style={styles.centered}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <Image source={require("../../assets/work-station/app-icon.png")} style={styles.logo} />
      <Text style={styles.eyebrow}>PRIVATE PERSONAL AI</Text>
      <Text accessibilityRole="header" style={styles.title}>Connect WORK STATION</Text>
      <Text style={styles.muted}>
        Enter an owner bearer token. It is stored in this device&apos;s Keychain or Keystore.
      </Text>
      <TextInput
        accessibilityLabel="Bearer token"
        value={token}
        onChangeText={setToken}
        secureTextEntry
        autoCapitalize="none"
        autoCorrect={false}
        textContentType="password"
        style={styles.input}
      />
      {error !== null && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}
      <Pressable
        accessibilityRole="button"
        disabled={token.trim().length === 0}
        style={[styles.primaryButton, token.trim().length === 0 && styles.disabled]}
        onPress={() => {
          const submitted = token.trim();
          setToken("");
          void connect(submitted);
        }}
      >
        <Text style={styles.primaryButtonText}>Connect</Text>
      </Pressable>
      <Text style={styles.footnote}>Provisioning remains an operator-only backend action.</Text>
    </KeyboardAvoidingView>
  );
}

export default function ChatScreen() {
  const { colors, styles } = useThemedStyles();
  const { state, client } = useWorkStation();
  const [models, setModels] = useState<LocalModel[]>([]);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversationCursor, setConversationCursor] = useState<ConversationCursor | null>(null);
  const [loadingMoreConversations, setLoadingMoreConversations] = useState(false);
  const [selected, setSelected] = useState<ConversationSummary | null>(null);
  const [conversationQuery, setConversationQuery] = useState("");
  const [conversationSearch, setConversationSearch] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [conversationTitle, setConversationTitle] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [messageCursor, setMessageCursor] = useState<number | null>(null);
  const [loadingMoreMessages, setLoadingMoreMessages] = useState(false);
  const [selectedModel, setSelectedModel] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
  const [editedMessageContent, setEditedMessageContent] = useState("");
  const [attachments, setAttachments] = useState<Asset[]>([]);
  const [busy, setBusy] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const generation = useRef<AbortController | null>(null);
  const conversationPageRequest = useRef<AbortController | null>(null);
  const messagePageRequest = useRef<AbortController | null>(null);
  const selectedConversation = useRef<ConversationSummary | null>(null);
  const recorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
  const recorderState = useAudioRecorderState(recorder);
  const visibleConversations = [...conversations].sort((left, right) =>
    Number(left.is_archived) - Number(right.is_archived) ||
    Number(right.is_pinned) - Number(left.is_pinned),
  );

  useEffect(() => {
    const timer = setTimeout(
      () => setConversationSearch(conversationQuery.trim()),
      300,
    );
    return () => clearTimeout(timer);
  }, [conversationQuery]);

  const listConversationPage = useCallback(
    (
      activeClient: MobileApiClient,
      cursor: ConversationCursor | null = null,
      signal?: AbortSignal,
    ) =>
      conversationSearch
        ? activeClient.searchConversations({
            query: conversationSearch,
            limit: 50,
            include_archived: showArchived,
            ...(cursor === null
              ? {}
              : {
                  cursor_updated_at: cursor.updated_at,
                  cursor_id: cursor.id,
                }),
          }, signal)
        : activeClient.listConversations({
            includeArchived: showArchived,
            ...(cursor === null ? {} : { cursor }),
            signal,
          }),
    [conversationSearch, showArchived],
  );

  const loadMessages = useCallback(async (
    conversation: ConversationSummary,
    signal?: AbortSignal,
  ) => {
    if (client === null) return;
    messagePageRequest.current?.abort();
    const controller = new AbortController();
    const abort = () => controller.abort();
    if (signal?.aborted) controller.abort();
    else signal?.addEventListener("abort", abort, { once: true });
    messagePageRequest.current = controller;
    selectedConversation.current = conversation;
    setSelected(conversation);
    setConversationTitle(conversation.title ?? "");
    setNotice(null);
    try {
      const page = await client.listMessages(conversation.id, controller.signal);
      if (messagePageRequest.current !== controller) return;
      setMessages(page.items);
      setMessageCursor(page.next_cursor);
    } catch (cause) {
      if (cause instanceof MobileApiError && cause.kind === "cancelled") return;
      setNotice(safeError(cause));
    } finally {
      signal?.removeEventListener("abort", abort);
      if (messagePageRequest.current === controller) {
        messagePageRequest.current = null;
      }
    }
  }, [client]);

  const loadMoreMessages = useCallback(async () => {
    if (
      client === null ||
      selected === null ||
      messageCursor === null ||
      loadingMoreMessages ||
      busy
    ) return;
    messagePageRequest.current?.abort();
    const controller = new AbortController();
    messagePageRequest.current = controller;
    setLoadingMoreMessages(true);
    setNotice(null);
    try {
      const page = await client.listMessagesPage(selected.id, {
        cursor: messageCursor,
        limit: 100,
        signal: controller.signal,
      });
      if (messagePageRequest.current !== controller) return;
      setMessages((current) => mergeMessages(current, page.items));
      setMessageCursor(page.next_cursor);
    } catch (cause) {
      if (cause instanceof MobileApiError && cause.kind === "cancelled") return;
      setNotice(safeError(cause));
    } finally {
      if (messagePageRequest.current === controller) {
        messagePageRequest.current = null;
        setLoadingMoreMessages(false);
      }
    }
  }, [busy, client, loadingMoreMessages, messageCursor, selected]);

  const loadWorkspace = useCallback(async () => {
    if (client === null) return;
    conversationPageRequest.current?.abort();
    const controller = new AbortController();
    conversationPageRequest.current = controller;
    setLoadingMoreConversations(false);
    setBusy(true);
    setNotice(null);
    try {
      const [modelPage, conversationPage] = await Promise.all([
        client.listModels(controller.signal),
        listConversationPage(client, null, controller.signal),
      ]);
      const available = modelPage.items.filter(modelCanChat);
      setModels(modelPage.items);
      setSelectedModel((current) =>
        available.some((model) => model.model_id === current)
          ? current
          : available[0]?.model_id ?? null,
      );
      setConversations(conversationPage.items);
      setConversationCursor(conversationPage.next_cursor);
      if (selectedConversation.current === null && conversationPage.items[0]) {
        await loadMessages(conversationPage.items[0], controller.signal);
      }
    } catch (cause) {
      if (cause instanceof MobileApiError && cause.kind === "cancelled") return;
      setNotice(safeError(cause));
    } finally {
      if (conversationPageRequest.current === controller) {
        conversationPageRequest.current = null;
        setBusy(false);
      }
    }
  }, [client, listConversationPage, loadMessages]);

  const loadMoreConversations = useCallback(async () => {
    if (
      client === null ||
      conversationCursor === null ||
      loadingMoreConversations ||
      busy
    ) return;
    conversationPageRequest.current?.abort();
    const controller = new AbortController();
    conversationPageRequest.current = controller;
    setLoadingMoreConversations(true);
    setNotice(null);
    try {
      const page = await listConversationPage(
        client,
        conversationCursor,
        controller.signal,
      );
      setConversations((current) => mergeConversationSummaries(current, page.items));
      setConversationCursor(page.next_cursor);
    } catch (cause) {
      if (cause instanceof MobileApiError && cause.kind === "cancelled") return;
      setNotice(safeError(cause));
    } finally {
      if (conversationPageRequest.current === controller) {
        conversationPageRequest.current = null;
        setLoadingMoreConversations(false);
      }
    }
  }, [busy, client, conversationCursor, listConversationPage, loadingMoreConversations]);

  useEffect(() => {
    const timer = setTimeout(() => {
      if (state === "connected") void loadWorkspace();
    }, 0);
    return () => {
      clearTimeout(timer);
      conversationPageRequest.current?.abort();
    };
  }, [loadWorkspace, state]);

  useEffect(
    () => () => {
      messagePageRequest.current?.abort();
    },
    [],
  );

  useEffect(() => {
    selectedConversation.current = selected;
  }, [selected]);

  if (state !== "connected" || client === null) return <ConnectScreen />;
  const connectedClient = client;

  async function upload(file: MobileUpload) {
    setBusy(true);
    setNotice(null);
    try {
      const asset = await connectedClient.uploadAsset(file);
      setAttachments((current) => [...current, asset]);
    } catch (cause) {
      setNotice(safeError(cause));
    } finally {
      setBusy(false);
    }
  }

  async function chooseDocument() {
    const result = await DocumentPicker.getDocumentAsync({
      type: ["application/pdf", "text/plain", "text/csv", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
      copyToCacheDirectory: true,
      multiple: false,
      base64: false,
    });
    if (!result.canceled) {
      const file = result.assets[0];
      if (file) await upload({ uri: file.uri, name: file.name, mimeType: file.mimeType ?? "application/octet-stream" });
    }
  }

  async function chooseImage(camera: boolean) {
    const result = camera
      ? await ImagePicker.launchCameraAsync({ mediaTypes: ["images"], quality: 0.9 })
      : await ImagePicker.launchImageLibraryAsync({ mediaTypes: ["images"], quality: 0.9 });
    if (!result.canceled) {
      const image = result.assets[0];
      if (image) {
        await upload({
          uri: image.uri,
          name: image.fileName ?? `private-image.${image.mimeType?.split("/")[1] ?? "jpg"}`,
          mimeType: image.mimeType ?? "image/jpeg",
        });
      }
    }
  }

  async function toggleVoice() {
    if (recorderState.isRecording) {
      await recorder.stop();
      const uri = recorder.uri;
      const voiceModel = models.find(
        (model) => model.runnable_now && model.capabilities.includes("speech_recognition"),
      );
      if (uri === null || voiceModel === undefined) {
        setNotice("Local speech recognition is unavailable.");
        return;
      }
      setBusy(true);
      let uploaded: Asset | null = null;
      try {
        uploaded = await connectedClient.uploadAsset({ uri, name: "voice-prompt.m4a", mimeType: "audio/mp4" });
        const transcript = await connectedClient.transcribe(uploaded.id, voiceModel.model_id);
        setPrompt(transcript.text);
      } catch (cause) {
        setNotice(safeError(cause));
      } finally {
        if (uploaded !== null) await connectedClient.deleteAsset(uploaded.id).catch(() => undefined);
        setBusy(false);
      }
      return;
    }
    const permission = await AudioModule.requestRecordingPermissionsAsync();
    if (!permission.granted) {
      setNotice("Microphone permission is required for a voice prompt.");
      return;
    }
    await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true });
    await recorder.prepareToRecordAsync();
    recorder.record();
  }

  async function send() {
    const text = prompt.trim();
    if (text.length === 0 || selectedModel === null || busy) return;
    const controller = new AbortController();
    generation.current = controller;
    setGenerating(true);
    setBusy(true);
    setNotice(null);
    setPrompt("");
    const attachmentIds = attachments.map((asset) => asset.id);
    setAttachments([]);
    try {
      let conversation = selected;
      if (conversation === null) {
        const created = await connectedClient.createConversation(
          { initial_message: text, ...(attachmentIds.length ? { attachment_ids: attachmentIds } : {}) },
          controller.signal,
        );
        conversation = created;
        setSelected(created);
        setConversationTitle(created.title ?? "");
        setConversations((current) => [created, ...current]);
        setMessages([created.initial_message]);
        setMessageCursor(null);
        await connectedClient.generate(created.id, { model_id: selectedModel }, controller.signal);
      } else {
        await connectedClient.generate(
          conversation.id,
          { model_id: selectedModel, user_message: text, ...(attachmentIds.length ? { attachment_ids: attachmentIds } : {}) },
          controller.signal,
        );
      }
      const messagePage = await connectedClient.listMessages(conversation.id);
      setMessages(messagePage.items);
      setMessageCursor(messagePage.next_cursor);
      const conversationPage = await listConversationPage(connectedClient);
      setConversations(conversationPage.items);
      setConversationCursor(conversationPage.next_cursor);
    } catch (cause) {
      setNotice(safeError(cause));
      if (selected !== null) await loadMessages(selected);
    } finally {
      generation.current = null;
      setGenerating(false);
      setBusy(false);
    }
  }

  async function renameSelected() {
    if (selected === null || busy) return;
    setBusy(true);
    setNotice(null);
    try {
      const normalized = conversationTitle.trim();
      const renamed = await connectedClient.renameConversation(selected.id, {
        title: normalized.length === 0 ? null : normalized,
      });
      setSelected(renamed);
      setConversationTitle(renamed.title ?? "");
      setConversations((current) =>
        current.map((conversation) =>
          conversation.id === renamed.id ? renamed : conversation,
        ),
      );
      if (conversationSearch) {
        const conversationPage = await listConversationPage(connectedClient);
        setConversations(conversationPage.items);
        setConversationCursor(conversationPage.next_cursor);
      }
    } catch (cause) {
      setNotice(safeError(cause));
    } finally {
      setBusy(false);
    }
  }

  async function duplicateSelected() {
    if (selected === null || busy) return;
    setBusy(true);
    setNotice(null);
    try {
      const fork = await connectedClient.forkConversation(selected.id);
      const page = await connectedClient.listMessages(fork.id);
      setSelected(fork);
      setConversationTitle(fork.title ?? "");
      setMessages(page.items);
      setMessageCursor(page.next_cursor);
      setConversations((current) => [
        fork,
        ...current.filter((item) => item.id !== fork.id),
      ]);
    } catch (cause) {
      setNotice(safeError(cause));
    } finally {
      setBusy(false);
    }
  }

  async function branchAndGenerate(
    userMessage: Message,
    replacementContent?: string,
  ) {
    if (busy || selectedModel === null || userMessage.attachments.length > 0) return;
    const controller = new AbortController();
    generation.current = controller;
    setBusy(true);
    setGenerating(true);
    setNotice(null);
    try {
      const fork = await connectedClient.forkConversation(
        userMessage.conversation_id,
        {
          through_sequence_number: userMessage.sequence_number,
          ...(replacementContent === undefined
            ? {}
            : { replacement_content: replacementContent }),
        },
        controller.signal,
      );
      setSelected(fork);
      setConversationTitle(fork.title ?? "");
      const forkPage = await connectedClient.listMessages(fork.id, controller.signal);
      setMessages(forkPage.items);
      setMessageCursor(forkPage.next_cursor);
      setEditingMessageId(null);
      setEditedMessageContent("");
      await connectedClient.generate(
        fork.id,
        { model_id: selectedModel },
        controller.signal,
      );
      const generatedPage = await connectedClient.listMessages(fork.id, controller.signal);
      setMessages(generatedPage.items);
      setMessageCursor(generatedPage.next_cursor);
      const conversationPage = await listConversationPage(connectedClient);
      setConversations(conversationPage.items);
      setConversationCursor(conversationPage.next_cursor);
    } catch (cause) {
      setNotice(safeError(cause));
    } finally {
      generation.current = null;
      setGenerating(false);
      setBusy(false);
    }
  }

  async function updateSelectedState(stateUpdate: ConversationStateUpdateRequest) {
    if (selected === null || busy) return;
    setBusy(true);
    setNotice(null);
    try {
      const updated = await connectedClient.updateConversationState(selected.id, stateUpdate);
      if (updated.is_archived && !showArchived) {
        setConversations((current) => current.filter((item) => item.id !== updated.id));
        setSelected(null);
        setConversationTitle("");
        setMessages([]);
        setMessageCursor(null);
      } else {
        setSelected(updated);
        setConversations((current) => current.map((item) => item.id === updated.id ? updated : item));
      }
    } catch (cause) {
      setNotice(safeError(cause));
    } finally {
      setBusy(false);
    }
  }

  function confirmDeleteSelected() {
    if (selected === null || busy) return;
    const conversation = selected;
    Alert.alert(
      "Delete conversation?",
      "Its private history and owned attachments will be removed.",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Delete",
          style: "destructive",
          onPress: () => {
            setBusy(true);
            setNotice(null);
            void connectedClient.deleteConversation(conversation.id)
              .then(() => {
                setConversations((current) =>
                  current.filter((item) => item.id !== conversation.id),
                );
                setSelected(null);
                setConversationTitle("");
                setMessages([]);
                setMessageCursor(null);
              })
              .catch((cause) => setNotice(safeError(cause)))
              .finally(() => setBusy(false));
          },
        },
      ],
    );
  }

  return (
    <SafeAreaView edges={["left", "right"]} style={styles.safe}>
      <View style={styles.connectionRow}>
        <View style={styles.connectedDot} />
        <Text style={styles.connectionText}>Connected</Text>
        <Pressable accessibilityRole="button" onPress={() => void loadWorkspace()}>
          <Text style={styles.link}>Refresh</Text>
        </Pressable>
      </View>
      <TextInput
        accessibilityLabel="Search all chats"
        value={conversationQuery}
        onChangeText={setConversationQuery}
        placeholder="Search all chats"
        placeholderTextColor={colors.subtle}
        style={styles.searchInput}
      />
      <Pressable
        accessibilityRole="checkbox"
        accessibilityState={{ checked: showArchived }}
        style={styles.archiveToggle}
        onPress={() => setShowArchived((current) => !current)}
      >
        <Text style={styles.link}>{showArchived ? "✓ " : ""}Show archived</Text>
      </Pressable>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chipRow}>
        <Pressable
          disabled={busy}
          style={styles.newChip}
          onPress={() => {
            messagePageRequest.current?.abort();
            setSelected(null);
            setConversationTitle("");
            setMessages([]);
            setMessageCursor(null);
          }}
        >
          <Text style={styles.primaryButtonText}>＋ New chat</Text>
        </Pressable>
        {visibleConversations.map((conversation) => (
          <Pressable
            key={conversation.id}
            disabled={busy}
            style={[styles.chip, selected?.id === conversation.id && styles.chipSelected]}
            onPress={() => void loadMessages(conversation)}
          >
            <Text numberOfLines={1} style={styles.chipText}>
              {conversation.is_pinned ? "★ " : ""}{conversation.title ?? "Conversation"}{conversation.is_archived ? " · archived" : ""}
            </Text>
          </Pressable>
        ))}
        {conversationCursor !== null && (
          <Pressable
            accessibilityRole="button"
            disabled={loadingMoreConversations || busy}
            style={styles.chip}
            onPress={() => void loadMoreConversations()}
          >
            <Text style={styles.chipText}>
              {loadingMoreConversations ? "Loading…" : "More chats"}
            </Text>
          </Pressable>
        )}
      </ScrollView>
      {selected !== null && (
        <View style={styles.conversationManager}>
          <TextInput
            accessibilityLabel="Conversation title"
            maxLength={255}
            value={conversationTitle}
            onChangeText={setConversationTitle}
            placeholder="Conversation title"
            placeholderTextColor={colors.subtle}
            style={styles.titleInput}
          />
          <Pressable
            accessibilityRole="button"
            disabled={busy}
            style={styles.manageButton}
            onPress={() => void renameSelected()}
          >
            <Text style={styles.link}>Save title</Text>
          </Pressable>
          {!selected.is_archived && (
            <Pressable
              accessibilityRole="button"
              disabled={busy}
              style={styles.manageButton}
              onPress={() => void updateSelectedState({ is_pinned: !selected.is_pinned })}
            >
              <Text style={styles.link}>{selected.is_pinned ? "Unpin" : "Pin"}</Text>
            </Pressable>
          )}
          <Pressable
            accessibilityRole="button"
            disabled={busy}
            style={styles.manageButton}
            onPress={() => void updateSelectedState({ is_archived: !selected.is_archived })}
          >
            <Text style={styles.link}>{selected.is_archived ? "Restore" : "Archive"}</Text>
          </Pressable>
          <Pressable
            accessibilityRole="button"
            disabled={busy}
            style={styles.manageButton}
            onPress={() => void duplicateSelected()}
          >
            <Text style={styles.link}>Duplicate</Text>
          </Pressable>
          <Pressable
            accessibilityRole="button"
            disabled={busy}
            style={styles.manageButton}
            onPress={confirmDeleteSelected}
          >
            <Text style={styles.recording}>Delete</Text>
          </Pressable>
        </View>
      )}
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.modelRow}>
        {models.filter(modelCanChat).map((model) => (
          <Pressable
            accessibilityRole="button"
            accessibilityState={{ selected: selectedModel === model.model_id }}
            key={model.model_id}
            style={[styles.modelChip, selectedModel === model.model_id && styles.modelSelected]}
            onPress={() => setSelectedModel(model.model_id)}
          >
            <Text style={styles.chipText}>{model.display_name}{model.parameter_class ? ` · ${model.parameter_class}` : ""}</Text>
          </Pressable>
        ))}
      </ScrollView>
      <FlatList
        style={styles.messageList}
        contentContainerStyle={styles.messageContent}
        data={messages}
        keyExtractor={(item) => String(item.id)}
        ListEmptyComponent={<Text style={styles.empty}>Start a private conversation with your Personal AI.</Text>}
        ListFooterComponent={messageCursor === null ? null : (
          <Pressable
            accessibilityRole="button"
            disabled={loadingMoreMessages || busy}
            style={styles.loadMoreMessages}
            onPress={() => void loadMoreMessages()}
          >
            <Text style={styles.link}>
              {loadingMoreMessages ? "Loading…" : "Load more messages"}
            </Text>
          </Pressable>
        )}
        renderItem={({ item, index }) => {
          const previousMessage = messages[index - 1];
          const editable = item.role === "user" && item.attachments.length === 0;
          const regeneratable =
            item.role === "assistant" &&
            previousMessage?.role === "user" &&
            previousMessage.attachments.length === 0;
          return (
            <View style={[styles.message, item.role === "user" ? styles.userMessage : styles.assistantMessage]}>
              <Text style={styles.messageRole}>{item.role}</Text>
              <Text selectable style={styles.messageText}>{item.content}</Text>
              {item.attachments.length > 0 && (
                <Text style={styles.attachmentMeta}>{item.attachments.length} private attachment(s)</Text>
              )}
              {item.citations.length > 0 && (
                <View style={styles.citationList}>
                  <Text accessibilityRole="header" style={styles.citationHeading}>
                    Sources
                  </Text>
                  {item.citations.map((citation) => {
                    const location = citationLocation(citation);
                    return (
                      <View
                        key={`${citation.asset_id}:${citation.position}`}
                        style={styles.citation}
                      >
                        <Text style={styles.citationSource}>
                          {citationSourceLabel(citation)}
                          {location ? ` · ${location}` : ""}
                        </Text>
                        {citation.state === "active" && citation.excerpt !== null && (
                          <Text selectable style={styles.citationExcerpt}>
                            {citation.excerpt}
                          </Text>
                        )}
                      </View>
                    );
                  })}
                </View>
              )}
              {editable && editingMessageId !== item.id && (
                <Pressable
                  accessibilityRole="button"
                  disabled={busy}
                  onPress={() => {
                    setEditingMessageId(item.id);
                    setEditedMessageContent(item.content);
                  }}
                >
                  <Text style={styles.messageAction}>Edit and resend in branch</Text>
                </Pressable>
              )}
              {editable && editingMessageId === item.id && (
                <View style={styles.inlineEditor}>
                  <TextInput
                    accessibilityLabel="Edit user message for a new immutable branch"
                    multiline
                    maxLength={100000}
                    value={editedMessageContent}
                    onChangeText={setEditedMessageContent}
                    style={styles.composerInput}
                  />
                  <View style={styles.messageActions}>
                    <Pressable
                      accessibilityRole="button"
                      disabled={busy || editedMessageContent.trim().length === 0}
                      onPress={() => void branchAndGenerate(item, editedMessageContent)}
                    >
                      <Text style={styles.messageAction}>Send edited branch</Text>
                    </Pressable>
                    <Pressable
                      accessibilityRole="button"
                      disabled={busy}
                      onPress={() => {
                        setEditingMessageId(null);
                        setEditedMessageContent("");
                      }}
                    >
                      <Text style={styles.messageAction}>Cancel</Text>
                    </Pressable>
                  </View>
                </View>
              )}
              {regeneratable && (
                <Pressable
                  accessibilityRole="button"
                  disabled={busy || selectedModel === null}
                  onPress={() => void branchAndGenerate(previousMessage)}
                >
                  <Text style={styles.messageAction}>Regenerate in branch</Text>
                </Pressable>
              )}
            </View>
          );
        }}
      />
      {notice !== null && <Text accessibilityRole="alert" style={styles.errorBanner}>{notice}</Text>}
      {attachments.length > 0 && (
        <ScrollView horizontal contentContainerStyle={styles.attachmentRow}>
          {attachments.map((asset) => <Text key={asset.id} style={styles.attachmentChip}>{asset.original_filename}</Text>)}
        </ScrollView>
      )}
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} keyboardVerticalOffset={92}>
        <View style={styles.composerTools}>
          <Pressable accessibilityRole="button" onPress={() => void chooseDocument()}><Text style={styles.link}>File</Text></Pressable>
          <Pressable accessibilityRole="button" onPress={() => void chooseImage(false)}><Text style={styles.link}>Photo</Text></Pressable>
          <Pressable accessibilityRole="button" onPress={() => void chooseImage(true)}><Text style={styles.link}>Camera</Text></Pressable>
          <Pressable accessibilityRole="button" onPress={() => void toggleVoice()}>
            <Text style={recorderState.isRecording ? styles.recording : styles.link}>
              {recorderState.isRecording ? "Stop recording" : "Voice"}
            </Text>
          </Pressable>
        </View>
        <View style={styles.composer}>
          <TextInput
            accessibilityLabel="Message"
            multiline
            value={prompt}
            onChangeText={setPrompt}
            placeholder="Message your Personal AI"
            placeholderTextColor={colors.subtle}
            style={styles.composerInput}
          />
          {busy && generating ? (
            <Pressable style={styles.cancelButton} onPress={() => generation.current?.abort()}>
              <Text style={styles.buttonText}>Stop</Text>
            </Pressable>
          ) : (
            <Pressable
              accessibilityRole="button"
              disabled={busy || prompt.trim().length === 0 || selectedModel === null}
              style={[styles.sendButton, (busy || prompt.trim().length === 0 || selectedModel === null) && styles.disabled]}
              onPress={() => void send()}
            >
              <Text style={styles.primaryButtonText}>Send</Text>
            </Pressable>
          )}
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function createStyles(colors: WorkStationColors) {
  return StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  centered: { flex: 1, justifyContent: "center", alignItems: "center", gap: 14, padding: 28, backgroundColor: colors.background },
  logo: { width: 76, height: 76, borderRadius: 18 },
  eyebrow: { color: colors.accent, fontSize: 12, fontWeight: "800", letterSpacing: 1.6 },
  title: { color: colors.text, fontSize: 26, fontWeight: "900", textAlign: "center" },
  muted: { color: colors.muted, fontSize: 15, lineHeight: 22, textAlign: "center" },
  footnote: { color: colors.subtle, fontSize: 12, textAlign: "center" },
  preserved: { color: colors.accent, borderColor: colors.accentBorder, borderWidth: 1, borderRadius: 12, padding: 12 },
  input: { width: "100%", minHeight: 52, color: colors.text, backgroundColor: colors.raised, borderColor: colors.line, borderWidth: 1, borderRadius: 12, padding: 14 },
  error: { width: "100%", color: colors.danger, backgroundColor: colors.dangerSoft, borderRadius: 10, padding: 12 },
  primaryButton: { minWidth: 150, minHeight: 48, justifyContent: "center", alignItems: "center", backgroundColor: colors.accent, borderRadius: 12, paddingHorizontal: 18 },
  primaryButtonText: { color: colors.onAccent, fontWeight: "900" },
  secondaryButton: { minWidth: 150, minHeight: 48, justifyContent: "center", alignItems: "center", borderColor: colors.line, borderWidth: 1, borderRadius: 12, paddingHorizontal: 18 },
  buttonText: { color: colors.text, fontWeight: "800" },
  disabled: { opacity: 0.45 },
  connectionRow: { minHeight: 42, flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 14, borderBottomColor: colors.line, borderBottomWidth: 1 },
  connectedDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: colors.accent },
  connectionText: { flex: 1, color: colors.accent, fontSize: 12, fontWeight: "800" },
  searchInput: { minHeight: 44, marginHorizontal: 12, marginTop: 8, color: colors.text, backgroundColor: colors.raised, borderColor: colors.line, borderWidth: 1, borderRadius: 12, paddingHorizontal: 12 },
  archiveToggle: { minHeight: 44, alignSelf: "flex-start", justifyContent: "center", marginLeft: 12 },
  link: { color: colors.accent, fontWeight: "800", paddingVertical: 8, paddingHorizontal: 4 },
  recording: { color: colors.danger, fontWeight: "900", paddingVertical: 8, paddingHorizontal: 4 },
  messageAction: { color: colors.accent, fontWeight: "800", paddingVertical: 8 },
  messageActions: { flexDirection: "row", flexWrap: "wrap", gap: 12 },
  inlineEditor: { gap: 6, marginTop: 8 },
  chipRow: { minHeight: 52, alignItems: "center", gap: 8, paddingHorizontal: 12 },
  chip: { maxWidth: 170, borderColor: colors.line, borderWidth: 1, borderRadius: 999, paddingHorizontal: 12, paddingVertical: 8 },
  chipSelected: { backgroundColor: colors.soft, borderColor: colors.accent },
  newChip: { borderRadius: 999, backgroundColor: colors.accent, paddingHorizontal: 12, paddingVertical: 9 },
  chipText: { color: colors.text, fontSize: 12, fontWeight: "700" },
  modelRow: { minHeight: 44, alignItems: "center", gap: 7, paddingHorizontal: 12, borderBottomColor: colors.line, borderBottomWidth: 1 },
  modelChip: { borderColor: colors.line, borderWidth: 1, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 6 },
  modelSelected: { borderColor: colors.accent, backgroundColor: colors.accentSoft },
  conversationManager: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 6, paddingHorizontal: 12, paddingBottom: 8, borderBottomColor: colors.line, borderBottomWidth: 1 },
  titleInput: { flex: 1, minHeight: 42, color: colors.text, backgroundColor: colors.raised, borderColor: colors.line, borderWidth: 1, borderRadius: 10, paddingHorizontal: 10 },
  manageButton: { minHeight: 42, justifyContent: "center" },
  messageList: { flex: 1 },
  messageContent: { padding: 14, gap: 12, flexGrow: 1 },
  empty: { color: colors.muted, textAlign: "center", marginTop: 80, lineHeight: 22 },
  message: { maxWidth: "90%", borderRadius: 16, padding: 13, borderWidth: 1 },
  userMessage: { alignSelf: "flex-end", backgroundColor: colors.accentSoft, borderColor: colors.accentBorder },
  assistantMessage: { alignSelf: "flex-start", backgroundColor: colors.raised, borderColor: colors.line },
  messageRole: { color: colors.muted, fontSize: 10, fontWeight: "900", textTransform: "uppercase", marginBottom: 5 },
  messageText: { color: colors.text, fontSize: 15, lineHeight: 22 },
  attachmentMeta: { color: colors.muted, fontSize: 11, marginTop: 8 },
  loadMoreMessages: { minHeight: 48, alignItems: "center", justifyContent: "center" },
  citationList: { gap: 7, marginTop: 8, borderTopColor: colors.line, borderTopWidth: 1, paddingTop: 8 },
  citationHeading: { color: colors.text, fontSize: 12, fontWeight: "900" },
  citation: { gap: 3 },
  citationSource: { color: colors.accent, fontSize: 12, fontWeight: "800" },
  citationExcerpt: { color: colors.muted, fontSize: 12, lineHeight: 18 },
  errorBanner: { color: colors.danger, backgroundColor: colors.dangerSoft, padding: 10, marginHorizontal: 12, borderRadius: 10 },
  attachmentRow: { gap: 6, paddingHorizontal: 12, paddingVertical: 6 },
  attachmentChip: { color: colors.accent, backgroundColor: colors.soft, borderRadius: 8, padding: 7, fontSize: 11 },
  composerTools: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 12 },
  composer: { flexDirection: "row", alignItems: "flex-end", gap: 8, padding: 12, borderTopColor: colors.line, borderTopWidth: 1, backgroundColor: colors.panel },
  composerInput: { flex: 1, minHeight: 48, maxHeight: 130, color: colors.text, backgroundColor: colors.raised, borderColor: colors.line, borderWidth: 1, borderRadius: 14, padding: 12 },
  sendButton: { minWidth: 68, minHeight: 48, justifyContent: "center", alignItems: "center", backgroundColor: colors.accent, borderRadius: 12 },
  cancelButton: { minWidth: 68, minHeight: 48, justifyContent: "center", alignItems: "center", borderColor: colors.danger, borderWidth: 1, borderRadius: 12 },
  });
}
