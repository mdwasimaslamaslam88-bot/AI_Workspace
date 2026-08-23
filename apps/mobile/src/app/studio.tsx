import type {
  LocalModel,
  MemoryCategory,
  PersonalMemory,
  ToolDescriptor,
  ToolExecution,
  Workflow,
} from "@work-station/shared";
import { setAudioModeAsync, useAudioPlayer, useAudioPlayerStatus } from "expo-audio";
import * as ImagePicker from "expo-image-picker";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Image,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  useColorScheme,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { MobileApiError } from "@/api/client";
import { useWorkStation } from "@/context/work-station";
import { cachePrivateMedia, type CachedPrivateMedia } from "@/media/private-cache";
import { notifyTaskFinished } from "@/notifications/private-notifications";
import { parseBoundedJsonObject } from "@/studio/input";
import { workStationColors, type WorkStationColors } from "@/theme/colors";

const memoryCategories: MemoryCategory[] = [
  "preference",
  "fact",
  "instruction",
  "project_context",
];

function safeError(cause: unknown): string {
  return cause instanceof MobileApiError
    ? cause.message
    : "The private operation could not be completed.";
}

function capabilityModels(models: LocalModel[], capability: LocalModel["capabilities"][number]) {
  return models.filter(
    (model) => model.runnable_now && model.capabilities.includes(capability),
  );
}

function replaceCache(
  reference: React.MutableRefObject<CachedPrivateMedia | null>,
  next: CachedPrivateMedia,
) {
  reference.current?.remove();
  reference.current = next;
}

export default function StudioScreen() {
  const scheme = useColorScheme();
  const colors = workStationColors(scheme);
  const styles = useMemo(() => createStyles(colors), [colors]);
  const { state, client } = useWorkStation();
  const [models, setModels] = useState<LocalModel[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [memories, setMemories] = useState<PersonalMemory[]>([]);
  const [memoryEnabled, setMemoryEnabled] = useState(true);
  const [memoryCategory, setMemoryCategory] = useState<MemoryCategory>("preference");
  const [memoryDraft, setMemoryDraft] = useState("");
  const [tools, setTools] = useState<ToolDescriptor[]>([]);
  const [toolName, setToolName] = useState<string | null>(null);
  const [toolArguments, setToolArguments] = useState("{}");
  const [lastExecution, setLastExecution] = useState<ToolExecution | null>(null);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [workflowName, setWorkflowName] = useState("");
  const [imageModelId, setImageModelId] = useState<string | null>(null);
  const [imagePrompt, setImagePrompt] = useState("");
  const [sourceAssetId, setSourceAssetId] = useState<string | null>(null);
  const [imageUri, setImageUri] = useState<string | null>(null);
  const [voiceModelId, setVoiceModelId] = useState<string | null>(null);
  const [voiceText, setVoiceText] = useState("");
  const [audioReady, setAudioReady] = useState(false);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const activeRequest = useRef<AbortController | null>(null);
  const imageCache = useRef<CachedPrivateMedia | null>(null);
  const audioCache = useRef<CachedPrivateMedia | null>(null);
  const player = useAudioPlayer(null);
  const playerStatus = useAudioPlayerStatus(player);

  const imageModels = useMemo(
    () => capabilityModels(models, "image_generation"),
    [models],
  );
  const voiceModels = useMemo(
    () => capabilityModels(models, "speech_synthesis"),
    [models],
  );
  const selectedTool = tools.find((tool) => tool.name === toolName) ?? null;

  const load = useCallback(async () => {
    if (client === null || state !== "connected") return;
    setBusyAction("Refreshing private studio");
    setNotice(null);
    try {
      const [modelPage, conversationPage, memoryPage, setting, toolPage, executionPage, workflowPage] =
        await Promise.all([
          client.listModels(),
          client.listConversations(),
          client.listMemories(),
          client.getMemorySetting(),
          client.listTools(),
          client.listToolExecutions({ limit: 10 }),
          client.listWorkflows({ limit: 20 }),
        ]);
      setModels(modelPage.items);
      setConversationId((current) =>
        conversationPage.items.some((conversation) => conversation.id === current)
          ? current
          : conversationPage.items[0]?.id ?? null,
      );
      setMemories(memoryPage.items);
      setMemoryEnabled(setting.enabled);
      setTools(toolPage.items);
      setToolName((current) =>
        toolPage.items.some((tool) => tool.name === current)
          ? current
          : toolPage.items[0]?.name ?? null,
      );
      setLastExecution(executionPage.items[0] ?? null);
      setWorkflows(workflowPage.items);
      const nextImageModels = capabilityModels(modelPage.items, "image_generation");
      const nextVoiceModels = capabilityModels(modelPage.items, "speech_synthesis");
      setImageModelId((current) =>
        nextImageModels.some((model) => model.model_id === current)
          ? current
          : nextImageModels[0]?.model_id ?? null,
      );
      setVoiceModelId((current) =>
        nextVoiceModels.some((model) => model.model_id === current)
          ? current
          : nextVoiceModels[0]?.model_id ?? null,
      );
    } catch (cause) {
      setNotice(safeError(cause));
    } finally {
      setBusyAction(null);
    }
  }, [client, state]);

  useEffect(() => {
    const timer = setTimeout(() => void load(), 0);
    return () => clearTimeout(timer);
  }, [load]);

  useEffect(() => () => {
    activeRequest.current?.abort();
    imageCache.current?.remove();
    audioCache.current?.remove();
  }, []);

  async function perform(
    label: string,
    operation: (signal: AbortSignal) => Promise<void>,
    notify = false,
  ) {
    if (busyAction !== null) return;
    const controller = new AbortController();
    activeRequest.current = controller;
    setBusyAction(label);
    setNotice(null);
    try {
      await operation(controller.signal);
      if (notify) await notifyTaskFinished(true).catch(() => undefined);
    } catch (cause) {
      setNotice(safeError(cause));
      if (notify && !(cause instanceof MobileApiError && cause.kind === "cancelled")) {
        await notifyTaskFinished(false).catch(() => undefined);
      }
    } finally {
      if (activeRequest.current === controller) activeRequest.current = null;
      setBusyAction(null);
    }
  }

  if (state !== "connected" || client === null) {
    return (
      <SafeAreaView edges={["left", "right"]} style={styles.safe}>
        <View style={styles.centered}>
          <Text accessibilityRole="header" style={styles.heading}>Private studio locked</Text>
          <Text style={styles.muted}>Connect the owner session from Chats to use AI capabilities.</Text>
        </View>
      </SafeAreaView>
    );
  }
  const connectedClient = client;

  async function addMemory() {
    const content = memoryDraft.trim();
    if (content.length === 0) return;
    await perform("Saving memory", async (signal) => {
      const memory = await connectedClient.createMemory(
        { category: memoryCategory, content },
        signal,
      );
      setMemories((current) => [memory, ...current]);
      setMemoryDraft("");
    });
  }

  async function forgetMemory(memoryId: string) {
    await perform("Forgetting memory", async (signal) => {
      await connectedClient.forgetMemory(memoryId, signal);
      setMemories((current) => current.filter((memory) => memory.id !== memoryId));
    });
  }

  async function toggleMemory(enabled: boolean) {
    await perform("Updating memory", async (signal) => {
      const setting = await connectedClient.updateMemorySetting(enabled, signal);
      setMemoryEnabled(setting.enabled);
    });
  }

  async function executeTool() {
    if (toolName === null) return;
    let argumentsValue;
    try {
      argumentsValue = parseBoundedJsonObject(toolArguments);
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "Tool arguments are invalid.");
      return;
    }
    await perform("Running bounded tool", async (signal) => {
      const execution = await connectedClient.executeTool(
        toolName,
        {
          arguments: argumentsValue,
          ...(conversationId === null ? {} : { conversation_id: conversationId }),
        },
        signal,
      );
      setLastExecution(execution);
    }, true);
  }

  async function createWorkflow() {
    if (toolName === null) return;
    let argumentsValue;
    try {
      argumentsValue = parseBoundedJsonObject(toolArguments);
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "Workflow arguments are invalid.");
      return;
    }
    await perform("Creating workflow", async (signal) => {
      const name = workflowName.trim();
      const workflow = await connectedClient.createWorkflow(
        {
          ...(name.length === 0 ? {} : { name }),
          steps: [{ tool_name: toolName, arguments: argumentsValue }],
        },
        signal,
      );
      setWorkflows((current) => [workflow, ...current]);
      setWorkflowName("");
    });
  }

  async function refreshWorkflow(workflowId: string) {
    await perform("Refreshing workflow", async (signal) => {
      const workflow = await connectedClient.getWorkflow(workflowId, signal);
      setWorkflows((current) =>
        current.map((item) => item.id === workflow.id ? workflow : item),
      );
    });
  }

  async function startWorkflow(workflowId: string) {
    await perform("Starting workflow", async (signal) => {
      const workflow = await connectedClient.startWorkflow(workflowId, signal);
      setWorkflows((current) =>
        current.map((item) => item.id === workflow.id ? workflow : item),
      );
    }, true);
  }

  async function cancelWorkflow(workflowId: string) {
    await perform("Cancelling workflow", async (signal) => {
      const workflow = await connectedClient.cancelWorkflow(workflowId, signal);
      setWorkflows((current) =>
        current.map((item) => item.id === workflow.id ? workflow : item),
      );
    });
  }

  async function showImage(assetId: string, signal: AbortSignal) {
    const cached = cachePrivateMedia(await connectedClient.downloadAsset(assetId, signal));
    replaceCache(imageCache, cached);
    setImageUri(cached.uri);
  }

  async function generateImage() {
    const prompt = imagePrompt.trim();
    if (prompt.length === 0 || imageModelId === null || conversationId === null) return;
    await perform("Generating private image", async (signal) => {
      const operation = await connectedClient.generateImage(
        { conversation_id: conversationId, model_id: imageModelId, prompt },
        signal,
      );
      await showImage(operation.asset.id, signal);
    }, true);
  }

  async function chooseEditImage() {
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ["images"],
      quality: 0.9,
    });
    const image = result.canceled ? undefined : result.assets[0];
    if (image === undefined) return;
    await perform("Uploading private source image", async (signal) => {
      const uploaded = await connectedClient.uploadAsset({
        uri: image.uri,
        name: image.fileName ?? `private-image.${image.mimeType?.split("/")[1] ?? "jpg"}`,
        mimeType: image.mimeType ?? "image/jpeg",
      }, signal);
      setSourceAssetId(uploaded.id);
      const cached = cachePrivateMedia(await connectedClient.downloadAsset(uploaded.id, signal));
      replaceCache(imageCache, cached);
      setImageUri(cached.uri);
    });
  }

  async function editImage() {
    const instruction = imagePrompt.trim();
    const model = models.find(
      (item) => item.model_id === imageModelId && item.capabilities.includes("image_editing"),
    );
    if (
      instruction.length === 0 ||
      model === undefined ||
      conversationId === null ||
      sourceAssetId === null
    ) return;
    await perform("Editing private image", async (signal) => {
      const operation = await connectedClient.editImage(
        {
          conversation_id: conversationId,
          model_id: model.model_id,
          source_asset_id: sourceAssetId,
          instruction,
        },
        signal,
      );
      await showImage(operation.asset.id, signal);
    }, true);
  }

  async function synthesizeVoice() {
    const text = voiceText.trim();
    if (text.length === 0 || voiceModelId === null) return;
    await perform("Creating private voice playback", async (signal) => {
      const synthesis = await connectedClient.synthesizeVoice(
        { model_id: voiceModelId, text },
        signal,
      );
      const cached = cachePrivateMedia(
        await connectedClient.downloadAsset(synthesis.asset.id, signal),
      );
      replaceCache(audioCache, cached);
      player.replace(cached.uri);
      await setAudioModeAsync({ allowsRecording: false, playsInSilentMode: true });
      player.play();
      setAudioReady(true);
    }, true);
  }

  return (
    <SafeAreaView edges={["left", "right"]} style={styles.safe}>
      <ScrollView contentContainerStyle={styles.content}>
        <View style={styles.statusRow} accessibilityLiveRegion="polite">
          <View style={styles.connectedDot} />
          <Text style={styles.statusText}>Owner session · private APIs only</Text>
          <Pressable accessibilityRole="button" style={styles.smallButton} onPress={() => void load()}>
            <Text style={styles.link}>Refresh</Text>
          </Pressable>
        </View>

        {busyAction !== null && (
          <View style={styles.progress} accessibilityLiveRegion="polite">
            <ActivityIndicator color={colors.accent} />
            <Text style={styles.muted}>{busyAction}…</Text>
            <Pressable accessibilityRole="button" onPress={() => activeRequest.current?.abort()}>
              <Text style={styles.danger}>Cancel</Text>
            </Pressable>
          </View>
        )}
        {notice !== null && <Text accessibilityRole="alert" style={styles.error}>{notice}</Text>}

        <View style={styles.card}>
          <View style={styles.cardTitleRow}>
            <View style={styles.titleGrow}>
              <Text style={styles.eyebrow}>PERSONAL MEMORY</Text>
              <Text accessibilityRole="header" style={styles.heading}>Owner context</Text>
            </View>
            <Switch
              accessibilityLabel="Personal memory"
              value={memoryEnabled}
              disabled={busyAction !== null}
              onValueChange={(enabled) => void toggleMemory(enabled)}
              trackColor={{ false: colors.line, true: colors.accentBorder }}
              thumbColor={memoryEnabled ? colors.accent : colors.muted}
            />
          </View>
          <ScrollView horizontal contentContainerStyle={styles.chipRow}>
            {memoryCategories.map((category) => (
              <Pressable
                accessibilityRole="button"
                accessibilityState={{ selected: memoryCategory === category }}
                key={category}
                style={[styles.chip, memoryCategory === category && styles.selectedChip]}
                onPress={() => setMemoryCategory(category)}
              >
                <Text style={styles.chipText}>{category.replaceAll("_", " ")}</Text>
              </Pressable>
            ))}
          </ScrollView>
          <TextInput
            accessibilityLabel="New personal memory"
            multiline
            maxLength={2_000}
            value={memoryDraft}
            onChangeText={setMemoryDraft}
            placeholder="Add an explicit fact, preference, instruction, or project context"
            placeholderTextColor={colors.subtle}
            style={styles.textArea}
          />
          <Pressable
            accessibilityRole="button"
            disabled={busyAction !== null || memoryDraft.trim().length === 0}
            style={[styles.primaryButton, (busyAction !== null || memoryDraft.trim().length === 0) && styles.disabled]}
            onPress={() => void addMemory()}
          >
            <Text style={styles.primaryButtonText}>Save private memory</Text>
          </Pressable>
          {memories.length === 0 ? <Text style={styles.muted}>No active explicit memories.</Text> : memories.map((memory) => (
            <View key={memory.id} style={styles.listItem}>
              <View style={styles.titleGrow}>
                <Text style={styles.itemLabel}>{memory.category.replaceAll("_", " ")}</Text>
                <Text selectable style={styles.itemText}>{memory.content}</Text>
              </View>
              <Pressable accessibilityRole="button" onPress={() => void forgetMemory(memory.id)}>
                <Text style={styles.danger}>Forget</Text>
              </Pressable>
            </View>
          ))}
        </View>

        <View style={styles.card}>
          <Text style={styles.eyebrow}>BOUNDED TOOLS</Text>
          <Text accessibilityRole="header" style={styles.heading}>Explicit owner execution</Text>
          <Text style={styles.muted}>Only the backend allowlist can run. Arguments must be a JSON object.</Text>
          <ScrollView horizontal contentContainerStyle={styles.chipRow}>
            {tools.map((tool) => (
              <Pressable
                accessibilityRole="button"
                accessibilityState={{ selected: toolName === tool.name }}
                key={tool.name}
                style={[styles.chip, toolName === tool.name && styles.selectedChip]}
                onPress={() => setToolName(tool.name)}
              >
                <Text style={styles.chipText}>{tool.name.replaceAll("_", " ")}</Text>
              </Pressable>
            ))}
          </ScrollView>
          {selectedTool !== null && (
            <Text style={styles.detail}>{selectedTool.description} · permission: {selectedTool.permission}</Text>
          )}
          <TextInput
            accessibilityLabel="Tool arguments as JSON"
            multiline
            autoCapitalize="none"
            autoCorrect={false}
            value={toolArguments}
            onChangeText={setToolArguments}
            placeholder={'{"expression":"2+2"}'}
            placeholderTextColor={colors.subtle}
            style={styles.codeInput}
          />
          <Pressable
            accessibilityRole="button"
            disabled={busyAction !== null || toolName === null}
            style={[styles.primaryButton, (busyAction !== null || toolName === null) && styles.disabled]}
            onPress={() => void executeTool()}
          >
            <Text style={styles.primaryButtonText}>Run selected tool</Text>
          </Pressable>
          {lastExecution !== null && (
            <View style={styles.result}>
              <Text style={styles.itemLabel}>{lastExecution.tool_name} · {lastExecution.status}</Text>
              <Text selectable style={styles.codeText}>
                {lastExecution.result === null
                  ? lastExecution.error_code ?? "No result"
                  : JSON.stringify(lastExecution.result, null, 2)}
              </Text>
            </View>
          )}
        </View>

        <View style={styles.card}>
          <Text style={styles.eyebrow}>BOUNDED WORKFLOWS</Text>
          <Text accessibilityRole="header" style={styles.heading}>Create, start, monitor</Text>
          <Text style={styles.muted}>Create one explicit step from the selected allowlisted tool and arguments above.</Text>
          <TextInput
            accessibilityLabel="Workflow name"
            maxLength={120}
            value={workflowName}
            onChangeText={setWorkflowName}
            placeholder="Optional workflow name"
            placeholderTextColor={colors.subtle}
            style={styles.input}
          />
          <Pressable
            accessibilityRole="button"
            disabled={busyAction !== null || toolName === null}
            style={styles.secondaryButton}
            onPress={() => void createWorkflow()}
          >
            <Text style={styles.buttonText}>Create workflow draft</Text>
          </Pressable>
          {workflows.map((workflow) => (
            <View key={workflow.id} style={styles.workflowItem}>
              <View style={styles.titleGrow}>
                <Text style={styles.itemText}>{workflow.name ?? "Private workflow"}</Text>
                <Text style={styles.detail}>{workflow.status} · {workflow.step_count} step(s)</Text>
              </View>
              {workflow.status === "pending" && (
                <Pressable accessibilityRole="button" onPress={() => void startWorkflow(workflow.id)}>
                  <Text style={styles.link}>Start</Text>
                </Pressable>
              )}
              {workflow.status === "running" && (
                <Pressable accessibilityRole="button" onPress={() => void cancelWorkflow(workflow.id)}>
                  <Text style={styles.danger}>Cancel</Text>
                </Pressable>
              )}
              <Pressable accessibilityRole="button" onPress={() => void refreshWorkflow(workflow.id)}>
                <Text style={styles.link}>Check</Text>
              </Pressable>
            </View>
          ))}
        </View>

        <View style={styles.card}>
          <Text style={styles.eyebrow}>IMAGE STUDIO</Text>
          <Text accessibilityRole="header" style={styles.heading}>Generate or edit privately</Text>
          <ScrollView horizontal contentContainerStyle={styles.chipRow}>
            {imageModels.map((model) => (
              <Pressable
                accessibilityRole="button"
                accessibilityState={{ selected: imageModelId === model.model_id }}
                key={model.model_id}
                style={[styles.chip, imageModelId === model.model_id && styles.selectedChip]}
                onPress={() => setImageModelId(model.model_id)}
              >
                <Text style={styles.chipText}>{model.display_name}</Text>
              </Pressable>
            ))}
          </ScrollView>
          {imageModels.length === 0 && <Text style={styles.muted}>No image runtime is currently ready.</Text>}
          {conversationId === null && <Text style={styles.warning}>Create a chat before generating an image.</Text>}
          <TextInput
            accessibilityLabel="Image prompt or editing instruction"
            multiline
            maxLength={2_000}
            value={imagePrompt}
            onChangeText={setImagePrompt}
            placeholder="Describe the image or editing instruction"
            placeholderTextColor={colors.subtle}
            style={styles.textArea}
          />
          <View style={styles.buttonRow}>
            <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => void chooseEditImage()}>
              <Text style={styles.buttonText}>{sourceAssetId === null ? "Choose edit source" : "Change edit source"}</Text>
            </Pressable>
            <Pressable
              accessibilityRole="button"
              disabled={busyAction !== null || imagePrompt.trim().length === 0 || imageModelId === null || conversationId === null}
              style={styles.primaryButton}
              onPress={() => void generateImage()}
            >
              <Text style={styles.primaryButtonText}>Generate</Text>
            </Pressable>
            <Pressable
              accessibilityRole="button"
              disabled={busyAction !== null || sourceAssetId === null || imagePrompt.trim().length === 0}
              style={styles.secondaryButton}
              onPress={() => void editImage()}
            >
              <Text style={styles.buttonText}>Edit source</Text>
            </Pressable>
          </View>
          {imageUri !== null && (
            <Image
              accessibilityLabel="Private generated or edited image"
              source={{ uri: imageUri }}
              resizeMode="contain"
              style={styles.generatedImage}
            />
          )}
        </View>

        <View style={styles.card}>
          <Text style={styles.eyebrow}>VOICE OUTPUT</Text>
          <Text accessibilityRole="header" style={styles.heading}>Local speech synthesis</Text>
          <ScrollView horizontal contentContainerStyle={styles.chipRow}>
            {voiceModels.map((model) => (
              <Pressable
                accessibilityRole="button"
                accessibilityState={{ selected: voiceModelId === model.model_id }}
                key={model.model_id}
                style={[styles.chip, voiceModelId === model.model_id && styles.selectedChip]}
                onPress={() => setVoiceModelId(model.model_id)}
              >
                <Text style={styles.chipText}>{model.display_name}</Text>
              </Pressable>
            ))}
          </ScrollView>
          {voiceModels.length === 0 && <Text style={styles.muted}>No speech synthesis runtime is currently ready.</Text>}
          <TextInput
            accessibilityLabel="Text for private speech synthesis"
            multiline
            maxLength={2_000}
            value={voiceText}
            onChangeText={setVoiceText}
            placeholder="Text to speak"
            placeholderTextColor={colors.subtle}
            style={styles.textArea}
          />
          <View style={styles.buttonRow}>
            <Pressable
              accessibilityRole="button"
              disabled={busyAction !== null || voiceText.trim().length === 0 || voiceModelId === null}
              style={styles.primaryButton}
              onPress={() => void synthesizeVoice()}
            >
              <Text style={styles.primaryButtonText}>Create & play</Text>
            </Pressable>
            {audioReady && (
              <Pressable
                accessibilityRole="button"
                style={styles.secondaryButton}
                onPress={() => playerStatus.playing ? player.pause() : player.play()}
              >
                <Text style={styles.buttonText}>{playerStatus.playing ? "Pause" : "Play again"}</Text>
              </Pressable>
            )}
          </View>
        </View>

        <Text style={styles.safety}>
          Private media is authenticated, cached only in app storage for display or playback, and removed when this screen closes. Credentials never enter URLs or notifications.
        </Text>
      </ScrollView>
    </SafeAreaView>
  );
}

function createStyles(colors: WorkStationColors) {
  return StyleSheet.create({
    safe: { flex: 1, backgroundColor: colors.background },
    centered: { flex: 1, justifyContent: "center", alignItems: "center", gap: 12, padding: 28 },
    content: { padding: 14, gap: 14, paddingBottom: 42 },
    statusRow: { minHeight: 44, flexDirection: "row", alignItems: "center", gap: 8, borderColor: colors.accentBorder, borderWidth: 1, borderRadius: 12, backgroundColor: colors.accentSoft, paddingHorizontal: 12 },
    connectedDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: colors.accent },
    statusText: { flex: 1, color: colors.accent, fontSize: 12, fontWeight: "800" },
    smallButton: { minHeight: 44, justifyContent: "center" },
    progress: { minHeight: 48, flexDirection: "row", alignItems: "center", gap: 10, borderColor: colors.line, borderWidth: 1, borderRadius: 12, padding: 12, backgroundColor: colors.panel },
    card: { gap: 10, borderColor: colors.line, borderWidth: 1, borderRadius: 16, backgroundColor: colors.raised, padding: 15 },
    cardTitleRow: { flexDirection: "row", alignItems: "center", gap: 12 },
    titleGrow: { flex: 1, gap: 4 },
    eyebrow: { color: colors.accent, fontSize: 11, fontWeight: "900", letterSpacing: 1.3 },
    heading: { color: colors.text, fontSize: 20, fontWeight: "900" },
    muted: { color: colors.muted, lineHeight: 20 },
    detail: { color: colors.subtle, fontSize: 12, lineHeight: 18 },
    warning: { color: colors.danger, backgroundColor: colors.dangerSoft, borderRadius: 9, padding: 9 },
    safety: { color: colors.subtle, fontSize: 12, lineHeight: 18, textAlign: "center", paddingHorizontal: 10 },
    error: { color: colors.danger, backgroundColor: colors.dangerSoft, borderRadius: 10, padding: 11 },
    danger: { color: colors.danger, fontWeight: "900", paddingHorizontal: 4, paddingVertical: 10 },
    link: { color: colors.accent, fontWeight: "900", paddingHorizontal: 4, paddingVertical: 10 },
    chipRow: { gap: 7, paddingVertical: 2 },
    chip: { minHeight: 40, justifyContent: "center", borderColor: colors.line, borderWidth: 1, borderRadius: 999, paddingHorizontal: 12 },
    selectedChip: { borderColor: colors.accent, backgroundColor: colors.accentSoft },
    chipText: { color: colors.text, fontSize: 12, fontWeight: "700", textTransform: "capitalize" },
    input: { minHeight: 48, color: colors.text, borderColor: colors.line, borderWidth: 1, borderRadius: 11, backgroundColor: colors.background, paddingHorizontal: 12 },
    textArea: { minHeight: 88, color: colors.text, borderColor: colors.line, borderWidth: 1, borderRadius: 11, backgroundColor: colors.background, padding: 12, textAlignVertical: "top" },
    codeInput: { minHeight: 92, color: colors.text, borderColor: colors.line, borderWidth: 1, borderRadius: 11, backgroundColor: colors.background, padding: 12, textAlignVertical: "top", fontFamily: "monospace" },
    primaryButton: { minHeight: 46, alignSelf: "flex-start", justifyContent: "center", alignItems: "center", backgroundColor: colors.accent, borderRadius: 11, paddingHorizontal: 15 },
    primaryButtonText: { color: colors.onAccent, fontWeight: "900" },
    secondaryButton: { minHeight: 46, alignSelf: "flex-start", justifyContent: "center", alignItems: "center", borderColor: colors.line, borderWidth: 1, borderRadius: 11, paddingHorizontal: 14 },
    buttonText: { color: colors.text, fontWeight: "800" },
    disabled: { opacity: 0.45 },
    buttonRow: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
    listItem: { flexDirection: "row", alignItems: "center", gap: 10, borderTopColor: colors.line, borderTopWidth: 1, paddingTop: 10 },
    itemLabel: { color: colors.accent, fontSize: 11, fontWeight: "900", textTransform: "uppercase" },
    itemText: { color: colors.text, lineHeight: 20 },
    result: { borderColor: colors.line, borderWidth: 1, borderRadius: 10, backgroundColor: colors.background, padding: 10, gap: 6 },
    codeText: { color: colors.text, fontFamily: "monospace", fontSize: 12, lineHeight: 18 },
    workflowItem: { minHeight: 54, flexDirection: "row", alignItems: "center", gap: 8, borderTopColor: colors.line, borderTopWidth: 1, paddingTop: 9 },
    generatedImage: { width: "100%", aspectRatio: 1, borderRadius: 12, backgroundColor: colors.background },
  });
}
