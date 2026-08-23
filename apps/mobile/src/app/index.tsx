import type { Asset, ConversationSummary, LocalModel, Message } from "@work-station/shared";
import {
  AudioModule,
  RecordingPresets,
  setAudioModeAsync,
  useAudioRecorder,
  useAudioRecorderState,
} from "expo-audio";
import * as DocumentPicker from "expo-document-picker";
import * as ImagePicker from "expo-image-picker";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Image,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import type { MobileUpload } from "@/api/client";
import { MobileApiError } from "@/api/client";
import { useWorkStation } from "@/context/work-station";

function safeError(cause: unknown): string {
  return cause instanceof MobileApiError
    ? cause.message
    : "The private operation could not be completed.";
}

function modelCanChat(model: LocalModel): boolean {
  return model.runnable_now && model.capabilities.includes("text_generation");
}

function ConnectScreen() {
  const { state, error, connect, retry, logout } = useWorkStation();
  const [token, setToken] = useState("");
  const disconnected = state === "offline" || state === "backend_unavailable";

  if (state === "connecting") {
    return (
      <View style={styles.centered} accessibilityLiveRegion="polite">
        <Image source={require("../../assets/work-station/app-icon.png")} style={styles.logo} />
        <ActivityIndicator color="#68efc8" size="large" />
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
  const { state, client } = useWorkStation();
  const [models, setModels] = useState<LocalModel[]>([]);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [selected, setSelected] = useState<ConversationSummary | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [selectedModel, setSelectedModel] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");
  const [attachments, setAttachments] = useState<Asset[]>([]);
  const [busy, setBusy] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const generation = useRef<AbortController | null>(null);
  const recorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
  const recorderState = useAudioRecorderState(recorder);

  const loadMessages = useCallback(async (conversation: ConversationSummary) => {
    if (client === null) return;
    setSelected(conversation);
    setNotice(null);
    try {
      setMessages((await client.listMessages(conversation.id)).items);
    } catch (cause) {
      setNotice(safeError(cause));
    }
  }, [client]);

  const loadWorkspace = useCallback(async () => {
    if (client === null) return;
    setBusy(true);
    setNotice(null);
    try {
      const [modelPage, conversationPage] = await Promise.all([
        client.listModels(),
        client.listConversations(),
      ]);
      const available = modelPage.items.filter(modelCanChat);
      setModels(modelPage.items);
      setSelectedModel((current) =>
        available.some((model) => model.model_id === current)
          ? current
          : available[0]?.model_id ?? null,
      );
      setConversations(conversationPage.items);
      if (selected === null && conversationPage.items[0]) {
        await loadMessages(conversationPage.items[0]);
      }
    } catch (cause) {
      setNotice(safeError(cause));
    } finally {
      setBusy(false);
    }
  }, [client, loadMessages, selected]);

  useEffect(() => {
    const timer = setTimeout(() => {
      if (state === "connected") void loadWorkspace();
    }, 0);
    return () => clearTimeout(timer);
  }, [loadWorkspace, state]);

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
        setConversations((current) => [created, ...current]);
        setMessages([created.initial_message]);
        await connectedClient.generate(created.id, { model_id: selectedModel }, controller.signal);
      } else {
        await connectedClient.generate(
          conversation.id,
          { model_id: selectedModel, user_message: text, ...(attachmentIds.length ? { attachment_ids: attachmentIds } : {}) },
          controller.signal,
        );
      }
      setMessages((await connectedClient.listMessages(conversation.id)).items);
      setConversations((await connectedClient.listConversations()).items);
    } catch (cause) {
      setNotice(safeError(cause));
      if (selected !== null) await loadMessages(selected);
    } finally {
      generation.current = null;
      setGenerating(false);
      setBusy(false);
    }
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
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chipRow}>
        <Pressable style={styles.newChip} onPress={() => { setSelected(null); setMessages([]); }}>
          <Text style={styles.primaryButtonText}>＋ New chat</Text>
        </Pressable>
        {conversations.map((conversation) => (
          <Pressable
            key={conversation.id}
            style={[styles.chip, selected?.id === conversation.id && styles.chipSelected]}
            onPress={() => void loadMessages(conversation)}
          >
            <Text numberOfLines={1} style={styles.chipText}>{conversation.title ?? "Conversation"}</Text>
          </Pressable>
        ))}
      </ScrollView>
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
        renderItem={({ item }) => (
          <View style={[styles.message, item.role === "user" ? styles.userMessage : styles.assistantMessage]}>
            <Text style={styles.messageRole}>{item.role}</Text>
            <Text selectable style={styles.messageText}>{item.content}</Text>
            {item.attachments.length > 0 && (
              <Text style={styles.attachmentMeta}>{item.attachments.length} private attachment(s)</Text>
            )}
          </View>
        )}
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
            placeholderTextColor="#718199"
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

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: "#040c1f" },
  centered: { flex: 1, justifyContent: "center", alignItems: "center", gap: 14, padding: 28, backgroundColor: "#040c1f" },
  logo: { width: 76, height: 76, borderRadius: 18 },
  eyebrow: { color: "#68efc8", fontSize: 12, fontWeight: "800", letterSpacing: 1.6 },
  title: { color: "#e8edf4", fontSize: 26, fontWeight: "900", textAlign: "center" },
  muted: { color: "#9ba9ba", fontSize: 15, lineHeight: 22, textAlign: "center" },
  footnote: { color: "#718199", fontSize: 12, textAlign: "center" },
  preserved: { color: "#68efc8", borderColor: "#1d6d62", borderWidth: 1, borderRadius: 12, padding: 12 },
  input: { width: "100%", minHeight: 52, color: "#e8edf4", backgroundColor: "#09152a", borderColor: "#263b58", borderWidth: 1, borderRadius: 12, padding: 14 },
  error: { width: "100%", color: "#ffb4ab", backgroundColor: "#3c1e22", borderRadius: 10, padding: 12 },
  primaryButton: { minWidth: 150, minHeight: 48, justifyContent: "center", alignItems: "center", backgroundColor: "#68efc8", borderRadius: 12, paddingHorizontal: 18 },
  primaryButtonText: { color: "#04251e", fontWeight: "900" },
  secondaryButton: { minWidth: 150, minHeight: 48, justifyContent: "center", alignItems: "center", borderColor: "#263b58", borderWidth: 1, borderRadius: 12, paddingHorizontal: 18 },
  buttonText: { color: "#e8edf4", fontWeight: "800" },
  disabled: { opacity: 0.45 },
  connectionRow: { minHeight: 42, flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 14, borderBottomColor: "#263b58", borderBottomWidth: 1 },
  connectedDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: "#68efc8" },
  connectionText: { flex: 1, color: "#68efc8", fontSize: 12, fontWeight: "800" },
  link: { color: "#68efc8", fontWeight: "800", paddingVertical: 8, paddingHorizontal: 4 },
  recording: { color: "#ffb4ab", fontWeight: "900", paddingVertical: 8, paddingHorizontal: 4 },
  chipRow: { minHeight: 52, alignItems: "center", gap: 8, paddingHorizontal: 12 },
  chip: { maxWidth: 170, borderColor: "#263b58", borderWidth: 1, borderRadius: 999, paddingHorizontal: 12, paddingVertical: 8 },
  chipSelected: { backgroundColor: "#14243c", borderColor: "#68efc8" },
  newChip: { borderRadius: 999, backgroundColor: "#68efc8", paddingHorizontal: 12, paddingVertical: 9 },
  chipText: { color: "#e8edf4", fontSize: 12, fontWeight: "700" },
  modelRow: { minHeight: 44, alignItems: "center", gap: 7, paddingHorizontal: 12, borderBottomColor: "#263b58", borderBottomWidth: 1 },
  modelChip: { borderColor: "#263b58", borderWidth: 1, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 6 },
  modelSelected: { borderColor: "#68efc8", backgroundColor: "#0a4b43" },
  messageList: { flex: 1 },
  messageContent: { padding: 14, gap: 12, flexGrow: 1 },
  empty: { color: "#9ba9ba", textAlign: "center", marginTop: 80, lineHeight: 22 },
  message: { maxWidth: "90%", borderRadius: 16, padding: 13, borderWidth: 1 },
  userMessage: { alignSelf: "flex-end", backgroundColor: "#0a4b43", borderColor: "#1d6d62" },
  assistantMessage: { alignSelf: "flex-start", backgroundColor: "#0e1c33", borderColor: "#263b58" },
  messageRole: { color: "#9ba9ba", fontSize: 10, fontWeight: "900", textTransform: "uppercase", marginBottom: 5 },
  messageText: { color: "#e8edf4", fontSize: 15, lineHeight: 22 },
  attachmentMeta: { color: "#9ba9ba", fontSize: 11, marginTop: 8 },
  errorBanner: { color: "#ffb4ab", backgroundColor: "#3c1e22", padding: 10, marginHorizontal: 12, borderRadius: 10 },
  attachmentRow: { gap: 6, paddingHorizontal: 12, paddingVertical: 6 },
  attachmentChip: { color: "#68efc8", backgroundColor: "#14243c", borderRadius: 8, padding: 7, fontSize: 11 },
  composerTools: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 12 },
  composer: { flexDirection: "row", alignItems: "flex-end", gap: 8, padding: 12, borderTopColor: "#263b58", borderTopWidth: 1, backgroundColor: "#07152f" },
  composerInput: { flex: 1, minHeight: 48, maxHeight: 130, color: "#e8edf4", backgroundColor: "#09152a", borderColor: "#263b58", borderWidth: 1, borderRadius: 14, padding: 12 },
  sendButton: { minWidth: 68, minHeight: 48, justifyContent: "center", alignItems: "center", backgroundColor: "#68efc8", borderRadius: 12 },
  cancelButton: { minWidth: 68, minHeight: 48, justifyContent: "center", alignItems: "center", borderColor: "#ffb4ab", borderWidth: 1, borderRadius: 12 },
});
