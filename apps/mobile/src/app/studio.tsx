import { isMarketingPublisherConnector } from "@work-station/shared";
import type {
  CreativeCapabilities,
  CreativeExperience,
  CreativeExperienceMode,
  FeatureRegistry,
  FinanceWorkspace,
  LearningProgram,
  Connector,
  ConnectorSettings,
  LocalModel,
  MarketingCampaign,
  MarketAssetClass,
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
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { MobileApiError } from "@/api/client";
import { useWorkStation } from "@/context/work-station";
import { cachePrivateMedia, type CachedPrivateMedia } from "@/media/private-cache";
import { notifyTaskFinished } from "@/notifications/private-notifications";
import { parseBoundedJsonObject } from "@/studio/input";
import type { WorkStationColors } from "@/theme/colors";
import { useWorkStationAppearance } from "@/theme/appearance";
import {
  isWorkflowTerminal,
  pollWorkflowUntilTerminal,
  WorkflowPollingTimeoutError,
} from "@/workflows/monitor";

const memoryCategories: MemoryCategory[] = [
  "preference",
  "fact",
  "instruction",
  "project_context",
];

const marketAssetClasses: MarketAssetClass[] = [
  "indian_stock",
  "global_stock",
  "crypto",
  "fx",
];

const creativeModes: CreativeExperienceMode[] = ["story", "game", "character"];

function safeError(cause: unknown): string {
  if (cause instanceof WorkflowPollingTimeoutError) return cause.message;
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
  const { colors } = useWorkStationAppearance();
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
  const [connectorSettings, setConnectorSettings] = useState<ConnectorSettings | null>(null);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [campaigns, setCampaigns] = useState<MarketingCampaign[]>([]);
  const [financeWorkspaces, setFinanceWorkspaces] = useState<FinanceWorkspace[]>([]);
  const [learningPrograms, setLearningPrograms] = useState<LearningProgram[]>([]);
  const [selectedLearningId, setSelectedLearningId] = useState<string | null>(null);
  const [learningSubject, setLearningSubject] = useState("");
  const [learningGoal, setLearningGoal] = useState("");
  const [learningTargetLanguage, setLearningTargetLanguage] = useState("ja");
  const [learningInstructionLanguage, setLearningInstructionLanguage] = useState("en");
  const [learningAnswer, setLearningAnswer] = useState("");
  const [reviewFront, setReviewFront] = useState("");
  const [reviewBack, setReviewBack] = useState("");
  const [creativeCapabilities, setCreativeCapabilities] = useState<CreativeCapabilities | null>(null);
  const [creativeExperiences, setCreativeExperiences] = useState<CreativeExperience[]>([]);
  const [selectedCreativeId, setSelectedCreativeId] = useState<string | null>(null);
  const [creativeMode, setCreativeMode] = useState<CreativeExperienceMode>("story");
  const [creativeTitle, setCreativeTitle] = useState("");
  const [creativePremise, setCreativePremise] = useState("");
  const [creativeGenre, setCreativeGenre] = useState("");
  const [creativeLanguage, setCreativeLanguage] = useState("en");
  const [creativeCharacter, setCreativeCharacter] = useState("");
  const [creativeTurn, setCreativeTurn] = useState("");
  const [selectedFinanceId, setSelectedFinanceId] = useState<string | null>(null);
  const [financeName, setFinanceName] = useState("");
  const [financeCurrency, setFinanceCurrency] = useState("USD");
  const [financeCash, setFinanceCash] = useState("");
  const [marketAssetClass, setMarketAssetClass] = useState<MarketAssetClass>("global_stock");
  const [marketSymbol, setMarketSymbol] = useState("");
  const [marketDisplayName, setMarketDisplayName] = useState("");
  const [marketSource, setMarketSource] = useState("");
  const [marketFact, setMarketFact] = useState("");
  const [paperQuantity, setPaperQuantity] = useState("");
  const [paperPrice, setPaperPrice] = useState("");
  const [campaignName, setCampaignName] = useState("");
  const [campaignObjective, setCampaignObjective] = useState("");
  const [campaignProduct, setCampaignProduct] = useState("");
  const [campaignAudience, setCampaignAudience] = useState("");
  const [campaignSourceReference, setCampaignSourceReference] = useState("");
  const [campaignSourceFact, setCampaignSourceFact] = useState("");
  const [campaignPublisherId, setCampaignPublisherId] = useState<string | null>(null);
  const [campaignPublishPath, setCampaignPublishPath] = useState("");
  const [analyticsSource, setAnalyticsSource] = useState("");
  const [analyticsValues, setAnalyticsValues] = useState(["", "", "", "", ""]);
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
  const [featureRegistry, setFeatureRegistry] = useState<FeatureRegistry | null>(null);
  const [selectedFeatureCategory, setSelectedFeatureCategory] = useState<string | null>(null);
  const activeRequest = useRef<AbortController | null>(null);
  const workflowMonitors = useRef(new Map<string, AbortController>());
  const campaignMonitors = useRef(new Map<string, AbortController>());
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
  const selectedFinance = useMemo(
    () => financeWorkspaces.find((workspace) => workspace.id === selectedFinanceId)
      ?? financeWorkspaces[0]
      ?? null,
    [financeWorkspaces, selectedFinanceId],
  );
  const selectedLearning = useMemo(
    () => learningPrograms.find((program) => program.id === selectedLearningId)
      ?? learningPrograms[0]
      ?? null,
    [learningPrograms, selectedLearningId],
  );
  const selectedCreative = useMemo(
    () => creativeExperiences.find((experience) => experience.id === selectedCreativeId)
      ?? creativeExperiences[0]
      ?? null,
    [creativeExperiences, selectedCreativeId],
  );

  const updateWorkflow = useCallback((workflow: Workflow) => {
    setWorkflows((current) =>
      current.map((item) => item.id === workflow.id ? workflow : item),
    );
  }, []);

  const monitorWorkflow = useCallback((workflowId: string) => {
    if (client === null || workflowMonitors.current.has(workflowId)) return;
    const controller = new AbortController();
    workflowMonitors.current.set(workflowId, controller);
    void pollWorkflowUntilTerminal(
      workflowId,
      controller.signal,
      (id, signal) => client.getWorkflow(id, signal),
      updateWorkflow,
    ).then((terminal) => {
      if (terminal !== null) {
        void notifyTaskFinished(terminal.status === "completed").catch(() => undefined);
      }
    }).catch((cause) => {
      if (controller.signal.aborted) return;
      setNotice(safeError(cause));
      void notifyTaskFinished(false).catch(() => undefined);
    }).finally(() => {
      workflowMonitors.current.delete(workflowId);
    });
  }, [client, updateWorkflow]);

  const updateCampaign = useCallback((campaign: MarketingCampaign) => {
    setCampaigns((current) => [campaign, ...current.filter((item) => item.id !== campaign.id)]);
  }, []);

  const monitorCampaign = useCallback((campaignId: string) => {
    if (client === null || campaignMonitors.current.has(campaignId)) return;
    const controller = new AbortController();
    campaignMonitors.current.set(campaignId, controller);
    void (async () => {
      try {
        for (let attempt = 0; attempt < 1_250; attempt += 1) {
          const campaign = await client.getMarketingCampaign(campaignId, controller.signal);
          if (controller.signal.aborted) return;
          updateCampaign(campaign);
          if (!["pending", "running", "publishing"].includes(campaign.status)) {
            if (["completed", "failed", "cancelled", "timed_out"].includes(campaign.status)) {
              await notifyTaskFinished(campaign.status === "completed").catch(() => undefined);
            }
            return;
          }
          await new Promise((resolve) => setTimeout(resolve, 500));
        }
        if (!controller.signal.aborted) setNotice("Campaign state exceeded its server deadline.");
      } catch (cause) {
        if (!controller.signal.aborted) setNotice(safeError(cause));
      } finally {
        campaignMonitors.current.delete(campaignId);
      }
    })();
  }, [client, updateCampaign]);

  const load = useCallback(async () => {
    if (client === null || state !== "connected") return;
    setBusyAction("Refreshing private studio");
    setNotice(null);
    try {
      const [modelPage, conversationPage, memoryPage, setting, toolPage, executionPage, workflowPage, registry, connectionSettings, connectorPage, campaignPage, financePage, learningPage, creativeCapabilityResult, creativePage] =
        await Promise.all([
          client.listModels(),
          client.listConversations(),
          client.listMemories(),
          client.getMemorySetting(),
          client.listTools(),
          client.listToolExecutions({ limit: 10 }),
          client.listWorkflows({ limit: 20 }),
          client.getFeatureRegistry().catch(() => null),
          client.getConnectorSettings().catch(() => null),
          client.listConnectors().catch(() => ({ items: [] })),
          client.listMarketingCampaigns().catch(() => ({ items: [] })),
          client.listFinanceWorkspaces().catch(() => ({ items: [] })),
          client.listLearningPrograms().catch(() => ({ items: [] })),
          client.getCreativeCapabilities().catch(() => null),
          client.listCreativeExperiences().catch(() => ({ items: [] })),
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
      if (registry !== null) setFeatureRegistry(registry);
      setConnectorSettings(connectionSettings);
      setConnectors(connectorPage.items);
      setCampaigns(campaignPage.items);
      setFinanceWorkspaces(financePage.items);
      setSelectedFinanceId((current) => financePage.items.some((workspace) => workspace.id === current)
        ? current
        : financePage.items[0]?.id ?? null);
      setLearningPrograms(learningPage.items);
      setSelectedLearningId((current) => learningPage.items.some((program) => program.id === current)
        ? current
        : learningPage.items[0]?.id ?? null);
      setCreativeCapabilities(creativeCapabilityResult);
      setCreativeExperiences(creativePage.items);
      setSelectedCreativeId((current) => creativePage.items.some((experience) => experience.id === current)
        ? current
        : creativePage.items[0]?.id ?? null);
      for (const workflow of workflowPage.items) {
        if (workflow.status === "running") monitorWorkflow(workflow.id);
      }
      for (const campaign of campaignPage.items) {
        if (["pending", "running", "publishing"].includes(campaign.status)) {
          monitorCampaign(campaign.id);
        }
      }
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
  }, [client, monitorCampaign, monitorWorkflow, state]);

  useEffect(() => {
    const timer = setTimeout(() => void load(), 0);
    return () => clearTimeout(timer);
  }, [load]);

  useEffect(() => () => {
    activeRequest.current?.abort();
    for (const controller of workflowMonitors.current.values()) controller.abort();
    workflowMonitors.current.clear();
    for (const controller of campaignMonitors.current.values()) controller.abort();
    campaignMonitors.current.clear();
    imageCache.current?.remove();
    audioCache.current?.remove();
  }, []);

  async function perform(
    label: string,
    operation: (signal: AbortSignal) => Promise<void>,
    notify: boolean | "failure_only" = false,
  ) {
    if (busyAction !== null) return;
    const controller = new AbortController();
    activeRequest.current = controller;
    setBusyAction(label);
    setNotice(null);
    try {
      await operation(controller.signal);
      if (notify === true) await notifyTaskFinished(true).catch(() => undefined);
    } catch (cause) {
      setNotice(safeError(cause));
      if (notify !== false && !(cause instanceof MobileApiError && cause.kind === "cancelled")) {
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
  const workspaceFeatures = featureRegistry?.items.filter((feature) =>
    feature.layer === "universal_workspace" || feature.layer === "apps_hub",
  ) ?? [];
  const featureCategories = [...new Set(workspaceFeatures.map((feature) => feature.category))];
  const visibleFeatures = selectedFeatureCategory === null
    ? []
    : workspaceFeatures.filter((feature) => feature.category === selectedFeatureCategory);

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
      updateWorkflow(workflow);
      if (isWorkflowTerminal(workflow.status)) {
        await notifyTaskFinished(workflow.status === "completed").catch(() => undefined);
      } else {
        monitorWorkflow(workflow.id);
      }
    }, "failure_only");
  }

  async function cancelWorkflow(workflowId: string) {
    await perform("Cancelling workflow", async (signal) => {
      const workflow = await connectedClient.cancelWorkflow(workflowId, signal);
      workflowMonitors.current.get(workflowId)?.abort();
      updateWorkflow(workflow);
    });
  }

  async function checkConnector(connectorId: string) {
    await perform("Checking connection", async (signal) => {
      await connectedClient.checkConnectorHealth(connectorId, signal);
      const page = await connectedClient.listConnectors(signal);
      setConnectors(page.items);
    });
  }

  async function revokeConnector(connectorId: string) {
    await perform("Revoking connection", async (signal) => {
      const revoked = await connectedClient.revokeConnector(connectorId, signal);
      setConnectors((current) => current.map((item) =>
        item.id === revoked.id ? revoked : item
      ));
    });
  }

  async function createCampaign() {
    const required = [campaignName, campaignObjective, campaignProduct, campaignAudience, campaignSourceReference, campaignSourceFact];
    if (required.some((value) => value.trim().length === 0)) return;
    await perform("Creating grounded campaign", async (signal) => {
      const campaign = await connectedClient.createMarketingCampaign({
        name: campaignName.trim(),
        objective: campaignObjective.trim(),
        product: campaignProduct.trim(),
        audience: campaignAudience.trim(),
        channels: ["email"],
        source_facts: [{
          source_reference: campaignSourceReference.trim(),
          fact: campaignSourceFact.trim(),
        }],
        ...(campaignPublisherId === null || campaignPublishPath.trim().length === 0 ? {} : {
          publisher_connector_id: campaignPublisherId,
          publish_path: campaignPublishPath.trim(),
        }),
      }, signal);
      updateCampaign(campaign);
      setCampaignName("");
      setCampaignObjective("");
      setCampaignProduct("");
      setCampaignAudience("");
      setCampaignSourceReference("");
      setCampaignSourceFact("");
    });
  }

  async function startCampaign(campaignId: string) {
    await perform("Starting verified campaign", async (signal) => {
      updateCampaign(await connectedClient.startMarketingCampaign(campaignId, signal));
      monitorCampaign(campaignId);
    }, "failure_only");
  }

  async function approveCampaign(campaignId: string) {
    await perform("Publishing approved campaign", async (signal) => {
      updateCampaign(await connectedClient.approveMarketingCampaign(campaignId, signal));
    }, "failure_only");
  }

  async function cancelCampaign(campaignId: string) {
    await perform("Cancelling campaign", async (signal) => {
      campaignMonitors.current.get(campaignId)?.abort();
      updateCampaign(await connectedClient.cancelMarketingCampaign(campaignId, signal));
    });
  }

  async function submitCampaignAnalytics(campaignId: string) {
    if (analyticsSource.trim().length === 0 || analyticsValues.some((value) => value.trim().length === 0)) return;
    await perform("Verifying campaign analytics", async (signal) => {
      updateCampaign(await connectedClient.submitMarketingAnalytics(campaignId, {
        source_reference: analyticsSource.trim(),
        observed_at: new Date().toISOString(),
        impressions: Number(analyticsValues[0]),
        clicks: Number(analyticsValues[1]),
        conversions: Number(analyticsValues[2]),
        spend_minor: Number(analyticsValues[3]),
        revenue_minor: Number(analyticsValues[4]),
      }, signal));
    }, true);
  }

  function positiveInteger(value: string): number | null {
    const parsed = Number(value);
    return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
  }

  async function createFinanceWorkspace() {
    const initialCash = positiveInteger(financeCash);
    if (financeName.trim().length === 0 || !/^[A-Z]{3}$/.test(financeCurrency) || initialCash === null) return;
    await perform("Creating paper workspace", async (signal) => {
      const workspace = await connectedClient.createFinanceWorkspace({
        name: financeName.trim(),
        base_currency: financeCurrency,
        initial_cash_minor: initialCash,
        max_order_bps: 1_000,
        max_position_bps: 2_500,
      }, signal);
      setFinanceWorkspaces((current) => [workspace, ...current]);
      setSelectedFinanceId(workspace.id);
      setFinanceName("");
      setFinanceCash("");
    });
  }

  function replaceFinanceWorkspace(workspace: FinanceWorkspace) {
    setFinanceWorkspaces((current) => [workspace, ...current.filter((item) => item.id !== workspace.id)]);
    setSelectedFinanceId(workspace.id);
  }

  async function addFinanceWatchItem() {
    if (selectedFinance === null || marketSymbol.trim().length === 0 || marketDisplayName.trim().length === 0) return;
    await perform("Adding watch item", async (signal) => {
      replaceFinanceWorkspace(await connectedClient.addMarketWatchItem(selectedFinance.id, {
        asset_class: marketAssetClass,
        symbol: marketSymbol.trim().toUpperCase(),
        display_name: marketDisplayName.trim(),
      }, signal));
      setMarketDisplayName("");
    });
  }

  async function runFinanceResearch() {
    if (selectedFinance === null || marketSymbol.trim().length === 0 || marketSource.trim().length === 0 || marketFact.trim().length === 0) return;
    await perform("Running grounded market research", async (signal) => {
      await connectedClient.runMarketResearch(selectedFinance.id, {
        kind: "research",
        asset_class: marketAssetClass,
        subject: marketSymbol.trim().toUpperCase(),
        source_reference: marketSource.trim(),
        source_facts: [{ source_reference: marketSource.trim(), fact: marketFact.trim() }],
      }, signal);
      replaceFinanceWorkspace(await connectedClient.getFinanceWorkspace(selectedFinance.id, signal));
      setMarketFact("");
    }, true);
  }

  async function executePaperOrder() {
    const quantity = positiveInteger(paperQuantity);
    const price = positiveInteger(paperPrice);
    if (selectedFinance === null || quantity === null || price === null || marketSymbol.trim().length === 0 || marketSource.trim().length === 0) return;
    await perform("Executing confirmed paper order", async (signal) => {
      await connectedClient.executePaperOrder(selectedFinance.id, {
        execution_mode: "paper",
        asset_class: marketAssetClass,
        symbol: marketSymbol.trim().toUpperCase(),
        side: "buy",
        quantity_micros: quantity,
        price_minor: price,
        observed_at: new Date().toISOString(),
        source_reference: marketSource.trim(),
        owner_confirmed: true,
      }, signal);
      replaceFinanceWorkspace(await connectedClient.getFinanceWorkspace(selectedFinance.id, signal));
      setPaperQuantity("");
      setPaperPrice("");
    }, true);
  }

  function replaceLearningProgram(program: LearningProgram) {
    setLearningPrograms((current) => [program, ...current.filter((item) => item.id !== program.id)]);
    setSelectedLearningId(program.id);
  }

  async function createLearningProgram() {
    if (
      learningSubject.trim().length === 0 || learningGoal.trim().length === 0 ||
      !/^[A-Za-z][A-Za-z0-9-]{1,34}$/.test(learningTargetLanguage) ||
      !/^[A-Za-z][A-Za-z0-9-]{1,34}$/.test(learningInstructionLanguage)
    ) return;
    await perform("Creating learning curriculum", async (signal) => {
      replaceLearningProgram(await connectedClient.createLearningProgram({
        subject: learningSubject.trim(),
        goal: learningGoal.trim(),
        target_language: learningTargetLanguage,
        instruction_language: learningInstructionLanguage,
        start_difficulty: 1,
        target_difficulty: 5,
        weekly_minutes: 150,
        adaptive_difficulty: true,
      }, signal));
      setLearningSubject("");
      setLearningGoal("");
    });
  }

  async function generateMobileLesson(lessonId: string) {
    if (selectedLearning === null) return;
    await perform("Generating verified lesson", async (signal) => {
      replaceLearningProgram(await connectedClient.generateLearningLesson(
        selectedLearning.id, lessonId, signal,
      ));
    }, true);
  }

  async function submitMobileLearningAttempt(activityId: string) {
    if (selectedLearning === null || learningAnswer.trim().length === 0) return;
    await perform("Checking learning answer", async (signal) => {
      const attempt = await connectedClient.submitLearningAttempt(
        selectedLearning.id, activityId, { answer: learningAnswer.trim() }, signal,
      );
      setNotice(attempt.feedback);
      replaceLearningProgram(await connectedClient.getLearningProgram(selectedLearning.id, signal));
      setLearningAnswer("");
    });
  }

  async function addMobileReviewItem() {
    if (selectedLearning === null || reviewFront.trim().length === 0 || reviewBack.trim().length === 0) return;
    await perform("Adding vocabulary review", async (signal) => {
      await connectedClient.createLearningReviewItem(selectedLearning.id, {
        front: reviewFront.trim(), back: reviewBack.trim(),
      }, signal);
      replaceLearningProgram(await connectedClient.getLearningProgram(selectedLearning.id, signal));
      setReviewFront("");
      setReviewBack("");
    });
  }

  async function reviewMobileItem(itemId: string, quality: number) {
    if (selectedLearning === null) return;
    await perform("Scheduling learning review", async (signal) => {
      await connectedClient.reviewLearningItem(selectedLearning.id, itemId, { quality }, signal);
      replaceLearningProgram(await connectedClient.getLearningProgram(selectedLearning.id, signal));
    });
  }

  function replaceCreativeExperience(experience: CreativeExperience) {
    setCreativeExperiences((current) => [experience, ...current.filter((item) => item.id !== experience.id)]);
    setSelectedCreativeId(experience.id);
  }

  async function createMobileCreativeExperience() {
    if (
      creativeTitle.trim().length === 0 || creativePremise.trim().length === 0 ||
      creativeGenre.trim().length === 0 ||
      !/^[A-Za-z][A-Za-z0-9-]{1,34}$/.test(creativeLanguage) ||
      (creativeMode === "character" && creativeCharacter.trim().length === 0)
    ) return;
    await perform("Creating creative experience", async (signal) => {
      replaceCreativeExperience(await connectedClient.createCreativeExperience({
        mode: creativeMode,
        title: creativeTitle.trim(),
        premise: creativePremise.trim(),
        genre: creativeGenre.trim(),
        language: creativeLanguage,
        character_name: creativeMode === "character" ? creativeCharacter.trim() : null,
      }, signal));
      setCreativeTitle("");
      setCreativePremise("");
      setCreativeGenre("");
      setCreativeCharacter("");
    });
  }

  async function addMobileCreativeTurn() {
    if (selectedCreative === null || creativeTurn.trim().length === 0) return;
    await perform("Generating verified creative turn", async (signal) => {
      replaceCreativeExperience(await connectedClient.addCreativeTurn(
        selectedCreative.id, { owner_input: creativeTurn.trim() }, signal,
      ));
      setCreativeTurn("");
    }, true);
  }

  async function completeMobileCreativeExperience() {
    if (selectedCreative === null || selectedCreative.turn_count === 0) return;
    await perform("Completing creative experience", async (signal) => {
      replaceCreativeExperience(await connectedClient.completeCreativeExperience(selectedCreative.id, signal));
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
        <View style={styles.card}>
          <Text style={styles.eyebrow}>UNIVERSAL WORKSPACE</Text>
          <Text accessibilityRole="header" style={styles.heading}>Dynamic module catalog</Text>
          <Text style={styles.muted}>
            {featureRegistry === null
              ? "The authenticated registry is unavailable; real studio controls remain below."
              : `${workspaceFeatures.length} workspace and app capabilities · ${featureRegistry.count} registered overall`}
          </Text>
          <ScrollView horizontal contentContainerStyle={styles.chipRow}>
            {featureCategories.map((category) => (
              <Pressable
                accessibilityRole="button"
                accessibilityState={{ selected: selectedFeatureCategory === category }}
                key={category}
                style={[styles.chip, selectedFeatureCategory === category && styles.selectedChip]}
                onPress={() => setSelectedFeatureCategory((current) => current === category ? null : category)}
              >
                <Text style={styles.chipText}>{category}</Text>
              </Pressable>
            ))}
          </ScrollView>
          {visibleFeatures.map((feature) => (
            <View key={feature.id} style={styles.featureRow}>
              <View style={styles.titleGrow}>
                <Text style={styles.itemText}>{feature.title}</Text>
                <Text style={styles.detail}>{feature.description}</Text>
              </View>
              <Text style={feature.status === "planned" ? styles.warning : styles.availability}>
                {feature.status.replaceAll("_", " ")}
              </Text>
            </View>
          ))}
        </View>
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
            <Pressable accessibilityRole="button" style={styles.touchAction} onPress={() => activeRequest.current?.abort()}>
              <Text style={styles.danger}>Cancel</Text>
            </Pressable>
          </View>
        )}
        {notice !== null && <Text accessibilityRole="alert" style={styles.error}>{notice}</Text>}

        <View style={styles.card}>
          <Text style={styles.eyebrow}>CONNECTED APPS</Text>
          <Text accessibilityRole="header" style={styles.heading}>Scoped connections</Text>
          <Text style={styles.muted}>
            Credentials stay encrypted and write-only on the workstation. Registration uses the protected desktop/web management surface.
          </Text>
          {connectorSettings?.configured !== true ? (
            <Text style={styles.warning}>Connector runtime is not configured.</Text>
          ) : connectorSettings.allowed_origins.length === 0 ? (
            <Text style={styles.warning}>No network egress origin is operator-approved.</Text>
          ) : connectors.length === 0 ? (
            <Text style={styles.muted}>No owner connections registered.</Text>
          ) : connectors.map((connector) => (
            <View key={connector.id} style={styles.listItem}>
              <View style={styles.titleGrow}>
                <Text style={styles.itemLabel}>{connector.name}</Text>
                <Text style={styles.detail}>
                  {connector.kind.replaceAll("_", " ")} · {connector.connection_status.replaceAll("_", " ")} · {connector.scopes.join(", ")}
                </Text>
                <Text style={styles.detail}>{connector.base_url}</Text>
              </View>
              {connector.revoked_at === null && <View>
                <Pressable accessibilityRole="button" style={styles.touchAction} onPress={() => void checkConnector(connector.id)}>
                  <Text style={styles.link}>Health</Text>
                </Pressable>
                <Pressable accessibilityRole="button" style={styles.touchAction} onPress={() => void revokeConnector(connector.id)}>
                  <Text style={styles.danger}>Revoke</Text>
                </Pressable>
              </View>}
            </View>
          ))}
        </View>

        <View style={styles.card}>
          <Text style={styles.eyebrow}>DIGITAL MARKETING</Text>
          <Text accessibilityRole="header" style={styles.heading}>Verified campaign pipeline</Text>
          <Text style={styles.muted}>Local agents create source-grounded artifacts. Publishing is disabled until you approve a write-scoped connection.</Text>
          <TextInput accessibilityLabel="Campaign name" maxLength={120} value={campaignName} onChangeText={setCampaignName} placeholder="Campaign name" placeholderTextColor={colors.subtle} style={styles.input} />
          <TextInput accessibilityLabel="Campaign objective" multiline maxLength={2_000} value={campaignObjective} onChangeText={setCampaignObjective} placeholder="Objective" placeholderTextColor={colors.subtle} style={styles.textArea} />
          <TextInput accessibilityLabel="Campaign product" maxLength={500} value={campaignProduct} onChangeText={setCampaignProduct} placeholder="Product or service" placeholderTextColor={colors.subtle} style={styles.input} />
          <TextInput accessibilityLabel="Campaign audience" maxLength={1_000} value={campaignAudience} onChangeText={setCampaignAudience} placeholder="Audience" placeholderTextColor={colors.subtle} style={styles.input} />
          <TextInput accessibilityLabel="Campaign source reference" maxLength={512} value={campaignSourceReference} onChangeText={setCampaignSourceReference} placeholder="Source reference, e.g. brief.md#section" placeholderTextColor={colors.subtle} style={styles.input} />
          <TextInput accessibilityLabel="Campaign source fact" multiline maxLength={2_000} value={campaignSourceFact} onChangeText={setCampaignSourceFact} placeholder="Grounded source fact" placeholderTextColor={colors.subtle} style={styles.textArea} />
          <Text style={styles.itemLabel}>Publisher (optional)</Text>
          <ScrollView horizontal contentContainerStyle={styles.chipRow}>
            <Pressable accessibilityRole="button" accessibilityState={{ selected: campaignPublisherId === null }} style={[styles.chip, campaignPublisherId === null && styles.selectedChip]} onPress={() => setCampaignPublisherId(null)}><Text style={styles.chipText}>Draft only</Text></Pressable>
            {connectors.filter(isMarketingPublisherConnector).map((connector) => <Pressable accessibilityRole="button" accessibilityState={{ selected: campaignPublisherId === connector.id }} key={connector.id} style={[styles.chip, campaignPublisherId === connector.id && styles.selectedChip]} onPress={() => setCampaignPublisherId(connector.id)}><Text style={styles.chipText}>{connector.name}</Text></Pressable>)}
          </ScrollView>
          {campaignPublisherId !== null && <TextInput accessibilityLabel="Campaign publish path" maxLength={512} value={campaignPublishPath} onChangeText={setCampaignPublishPath} placeholder="/v1/campaigns" placeholderTextColor={colors.subtle} style={styles.input} />}
          <Pressable accessibilityRole="button" disabled={busyAction !== null || [campaignName, campaignObjective, campaignProduct, campaignAudience, campaignSourceReference, campaignSourceFact].some((value) => value.trim().length === 0) || (campaignPublisherId !== null && campaignPublishPath.trim().length === 0)} style={[styles.primaryButton, busyAction !== null && styles.disabled]} onPress={() => void createCampaign()}><Text style={styles.primaryButtonText}>Create grounded campaign</Text></Pressable>
          {campaigns.length === 0 ? <Text style={styles.muted}>No campaigns yet.</Text> : campaigns.map((campaign) => <View key={campaign.id} style={styles.result}>
            <View style={styles.cardTitleRow}><View style={styles.titleGrow}><Text style={styles.itemText}>{campaign.name}</Text><Text style={styles.detail}>{campaign.product} · {campaign.status.replaceAll("_", " ")}</Text></View></View>
            {campaign.stages.map((stage) => <View key={stage.id} style={styles.featureRow}><Text style={styles.itemLabel}>{stage.kind}</Text><Text style={stage.status === "failed" ? styles.danger : styles.detail}>{stage.status}</Text></View>)}
            <View style={styles.buttonRow}>
              {campaign.status === "pending" && <Pressable accessibilityRole="button" style={styles.touchAction} onPress={() => void startCampaign(campaign.id)}><Text style={styles.link}>Start</Text></Pressable>}
              {campaign.status === "needs_approval" && campaign.publisher_connector_id !== null && <Pressable accessibilityRole="button" style={styles.touchAction} onPress={() => void approveCampaign(campaign.id)}><Text style={styles.link}>Approve & publish</Text></Pressable>}
              {campaign.status === "needs_approval" && campaign.publisher_connector_id === null && <Text style={styles.warning}>Publisher connection required; no publish was attempted.</Text>}
              {["pending", "running", "needs_approval", "awaiting_analytics"].includes(campaign.status) && <Pressable accessibilityRole="button" style={styles.touchAction} onPress={() => void cancelCampaign(campaign.id)}><Text style={styles.danger}>Cancel</Text></Pressable>}
            </View>
            {campaign.status === "awaiting_analytics" && <View style={styles.result}>
              <Text style={styles.itemLabel}>Source analytics</Text>
              <TextInput accessibilityLabel="Analytics source" maxLength={512} value={analyticsSource} onChangeText={setAnalyticsSource} placeholder="provider export reference" placeholderTextColor={colors.subtle} style={styles.input} />
              {["Impressions", "Clicks", "Conversions", "Spend minor units", "Revenue minor units"].map((label, index) => <TextInput accessibilityLabel={label} key={label} keyboardType="number-pad" value={analyticsValues[index]} onChangeText={(value) => setAnalyticsValues((current) => current.map((item, position) => position === index ? value : item))} placeholder={label} placeholderTextColor={colors.subtle} style={styles.input} />)}
              <Pressable accessibilityRole="button" style={styles.primaryButton} onPress={() => void submitCampaignAnalytics(campaign.id)}><Text style={styles.primaryButtonText}>Submit source analytics</Text></Pressable>
            </View>}
            {campaign.analytics !== null && <Text selectable style={styles.codeText}>{JSON.stringify(campaign.analytics, null, 2)}</Text>}
          </View>)}
        </View>

        <View style={styles.card}>
          <Text style={styles.eyebrow}>FINANCE INTELLIGENCE</Text>
          <Text accessibilityRole="header" style={styles.heading}>Grounded paper market lab</Text>
          <Text style={styles.muted}>Owner-supplied sources only. Orders are simulations; live brokers remain an external dependency.</Text>
          {financeWorkspaces.length === 0 ? <>
            <TextInput accessibilityLabel="Finance workspace name" maxLength={120} value={financeName} onChangeText={setFinanceName} placeholder="Paper portfolio name" placeholderTextColor={colors.subtle} style={styles.input} />
            <TextInput accessibilityLabel="Finance base currency" autoCapitalize="characters" maxLength={3} value={financeCurrency} onChangeText={(value) => setFinanceCurrency(value.toUpperCase())} placeholder="USD" placeholderTextColor={colors.subtle} style={styles.input} />
            <TextInput accessibilityLabel="Initial paper cash in minor units" keyboardType="number-pad" value={financeCash} onChangeText={setFinanceCash} placeholder="Initial cash, minor units" placeholderTextColor={colors.subtle} style={styles.input} />
            <Pressable accessibilityRole="button" disabled={busyAction !== null || financeName.trim().length === 0 || positiveInteger(financeCash) === null || !/^[A-Z]{3}$/.test(financeCurrency)} style={[styles.primaryButton, busyAction !== null && styles.disabled]} onPress={() => void createFinanceWorkspace()}><Text style={styles.primaryButtonText}>Create paper workspace</Text></Pressable>
          </> : <>
            <ScrollView horizontal contentContainerStyle={styles.chipRow}>
              {financeWorkspaces.map((workspace) => <Pressable accessibilityRole="button" accessibilityState={{ selected: selectedFinance?.id === workspace.id }} key={workspace.id} style={[styles.chip, selectedFinance?.id === workspace.id && styles.selectedChip]} onPress={() => setSelectedFinanceId(workspace.id)}><Text style={styles.chipText}>{workspace.name}</Text></Pressable>)}
            </ScrollView>
            {selectedFinance !== null && <>
              <View style={styles.result}>
                <Text style={styles.itemLabel}>Paper mode · live broker external</Text>
                <Text style={styles.itemText}>{selectedFinance.cash_minor} {selectedFinance.base_currency} minor units</Text>
                <Text style={styles.detail}>{selectedFinance.positions.length} position(s) · {selectedFinance.orders.length} order(s) · {selectedFinance.artifacts.length} verified artifact(s)</Text>
              </View>
              <Text style={styles.itemLabel}>Asset</Text>
              <ScrollView horizontal contentContainerStyle={styles.chipRow}>
                {marketAssetClasses.map((assetClass) => <Pressable accessibilityRole="button" accessibilityState={{ selected: marketAssetClass === assetClass }} key={assetClass} style={[styles.chip, marketAssetClass === assetClass && styles.selectedChip]} onPress={() => setMarketAssetClass(assetClass)}><Text style={styles.chipText}>{assetClass.replaceAll("_", " ")}</Text></Pressable>)}
              </ScrollView>
              <TextInput accessibilityLabel="Market symbol" autoCapitalize="characters" maxLength={24} value={marketSymbol} onChangeText={(value) => setMarketSymbol(value.toUpperCase())} placeholder="Symbol, e.g. AAPL" placeholderTextColor={colors.subtle} style={styles.input} />
              <TextInput accessibilityLabel="Market display name" maxLength={120} value={marketDisplayName} onChangeText={setMarketDisplayName} placeholder="Watchlist display name" placeholderTextColor={colors.subtle} style={styles.input} />
              <Pressable accessibilityRole="button" disabled={busyAction !== null || marketSymbol.trim().length === 0 || marketDisplayName.trim().length === 0} style={styles.secondaryButton} onPress={() => void addFinanceWatchItem()}><Text style={styles.buttonText}>Add to watchlist</Text></Pressable>
              {selectedFinance.watch_items.map((item) => <Text key={item.id} style={styles.detail}>{item.asset_class.replaceAll("_", " ")} · {item.symbol} · {item.display_name}</Text>)}
              <TextInput accessibilityLabel="Market source reference" maxLength={512} value={marketSource} onChangeText={setMarketSource} placeholder="Source or dataset reference" placeholderTextColor={colors.subtle} style={styles.input} />
              <TextInput accessibilityLabel="Grounded market fact" multiline maxLength={2_000} value={marketFact} onChangeText={setMarketFact} placeholder="Fact from that source" placeholderTextColor={colors.subtle} style={styles.textArea} />
              <Pressable accessibilityRole="button" disabled={busyAction !== null || marketSymbol.trim().length === 0 || marketSource.trim().length === 0 || marketFact.trim().length === 0} style={styles.primaryButton} onPress={() => void runFinanceResearch()}><Text style={styles.primaryButtonText}>Run verified research</Text></Pressable>
              <TextInput accessibilityLabel="Paper quantity in micro units" keyboardType="number-pad" value={paperQuantity} onChangeText={setPaperQuantity} placeholder="Quantity, micro-units" placeholderTextColor={colors.subtle} style={styles.input} />
              <TextInput accessibilityLabel="Paper quote in minor units" keyboardType="number-pad" value={paperPrice} onChangeText={setPaperPrice} placeholder="Observed price, minor units" placeholderTextColor={colors.subtle} style={styles.input} />
              <Text style={styles.warning}>The next action confirms a paper-only buy. It cannot place a broker order.</Text>
              <Pressable accessibilityRole="button" disabled={busyAction !== null || positiveInteger(paperQuantity) === null || positiveInteger(paperPrice) === null || marketSymbol.trim().length === 0 || marketSource.trim().length === 0} style={styles.secondaryButton} onPress={() => void executePaperOrder()}><Text style={styles.buttonText}>Confirm & submit paper buy</Text></Pressable>
              {selectedFinance.artifacts.slice(0, 5).map((artifact) => <View key={artifact.id} style={styles.result}><Text style={styles.itemLabel}>{artifact.kind} · verified</Text><Text style={styles.detail}>{artifact.model_id} · {artifact.source_reference}</Text><Text selectable style={styles.itemText}>{artifact.output}</Text></View>)}
            </>}
          </>}
        </View>

        <View style={styles.card}>
          <Text style={styles.eyebrow}>UNIVERSAL LEARNING</Text>
          <Text accessibilityRole="header" style={styles.heading}>AI Teacher</Text>
          <Text style={styles.muted}>Private curriculum, verified lessons, adaptive exact-answer practice, vocabulary, and spaced repetition. Pronunciation scoring remains unavailable until a verified provider is configured.</Text>
          {learningPrograms.length === 0 ? <>
            <TextInput accessibilityLabel="Learning subject" maxLength={160} value={learningSubject} onChangeText={setLearningSubject} placeholder="Subject, e.g. Japanese" placeholderTextColor={colors.subtle} style={styles.input} />
            <TextInput accessibilityLabel="Learning goal" multiline maxLength={2_000} value={learningGoal} onChangeText={setLearningGoal} placeholder="What you want to achieve" placeholderTextColor={colors.subtle} style={styles.textArea} />
            <TextInput accessibilityLabel="Learning target language" autoCapitalize="none" maxLength={35} value={learningTargetLanguage} onChangeText={setLearningTargetLanguage} placeholder="Target language tag, e.g. ja" placeholderTextColor={colors.subtle} style={styles.input} />
            <TextInput accessibilityLabel="Learning instruction language" autoCapitalize="none" maxLength={35} value={learningInstructionLanguage} onChangeText={setLearningInstructionLanguage} placeholder="Teaching language tag, e.g. en" placeholderTextColor={colors.subtle} style={styles.input} />
            <Pressable accessibilityRole="button" disabled={busyAction !== null || learningSubject.trim().length === 0 || learningGoal.trim().length === 0} style={[styles.primaryButton, busyAction !== null && styles.disabled]} onPress={() => void createLearningProgram()}><Text style={styles.primaryButtonText}>Create curriculum</Text></Pressable>
          </> : <>
            <ScrollView horizontal contentContainerStyle={styles.chipRow}>
              {learningPrograms.map((program) => <Pressable accessibilityRole="button" accessibilityState={{ selected: selectedLearning?.id === program.id }} key={program.id} style={[styles.chip, selectedLearning?.id === program.id && styles.selectedChip]} onPress={() => setSelectedLearningId(program.id)}><Text style={styles.chipText}>{program.subject}</Text></Pressable>)}
            </ScrollView>
            {selectedLearning !== null && <>
              <View style={styles.result}><Text style={styles.itemLabel}>{selectedLearning.status} · difficulty {selectedLearning.current_difficulty}/5</Text><Text style={styles.itemText}>{selectedLearning.completed_lessons}/{selectedLearning.total_lessons} lessons · {selectedLearning.progress_bps / 100}%</Text><Text style={styles.detail}>{selectedLearning.target_language} content taught in {selectedLearning.instruction_language}</Text></View>
              {selectedLearning.lessons.map((lesson) => <View key={lesson.id} style={styles.result}>
                <Text style={styles.itemLabel}>Lesson {lesson.position} · {lesson.status}</Text>
                <Text style={styles.itemText}>{lesson.title}</Text>
                {lesson.status === "planned" && <Pressable accessibilityRole="button" disabled={busyAction !== null} style={styles.primaryButton} onPress={() => void generateMobileLesson(lesson.id)}><Text style={styles.primaryButtonText}>Generate verified lesson</Text></Pressable>}
                {lesson.content !== null && <Text selectable style={styles.itemText}>{lesson.content}</Text>}
                {lesson.activities.map((activity) => <View key={activity.id} style={styles.result}><Text style={styles.detail}>{activity.kind} · {activity.prompt}</Text><TextInput accessibilityLabel={`Answer for ${activity.prompt}`} maxLength={4_000} value={learningAnswer} onChangeText={setLearningAnswer} placeholder="Your answer" placeholderTextColor={colors.subtle} style={styles.input} /><Pressable accessibilityRole="button" disabled={busyAction !== null || learningAnswer.trim().length === 0 || activity.attempts.length >= activity.max_attempts} style={styles.secondaryButton} onPress={() => void submitMobileLearningAttempt(activity.id)}><Text style={styles.buttonText}>Check answer</Text></Pressable></View>)}
              </View>)}
              <Text style={styles.itemLabel}>Vocabulary and spaced repetition</Text>
              <TextInput accessibilityLabel="Review card front" maxLength={1_000} value={reviewFront} onChangeText={setReviewFront} placeholder="Prompt or term" placeholderTextColor={colors.subtle} style={styles.input} />
              <TextInput accessibilityLabel="Review card back" multiline maxLength={2_000} value={reviewBack} onChangeText={setReviewBack} placeholder="Answer or translation" placeholderTextColor={colors.subtle} style={styles.textArea} />
              <Pressable accessibilityRole="button" disabled={busyAction !== null || reviewFront.trim().length === 0 || reviewBack.trim().length === 0} style={styles.secondaryButton} onPress={() => void addMobileReviewItem()}><Text style={styles.buttonText}>Add review card</Text></Pressable>
              {selectedLearning.review_items.map((item) => <View key={item.id} style={styles.listItem}><View style={styles.titleGrow}><Text style={styles.itemText}>{item.front} — {item.back}</Text><Text style={styles.detail}>Due {new Date(item.due_at).toLocaleDateString()} · interval {item.interval_days} day(s)</Text></View><Pressable accessibilityRole="button" style={styles.touchAction} onPress={() => void reviewMobileItem(item.id, 4)}><Text style={styles.link}>Recalled</Text></Pressable></View>)}
              <Text style={styles.warning}>Pronunciation scoring: external dependency. No local score is fabricated.</Text>
            </>}
          </>}
        </View>

        <View style={styles.card}>
          <Text style={styles.eyebrow}>CREATIVE EXPERIENCES</Text>
          <Text accessibilityRole="header" style={styles.heading}>Stories, games & characters</Text>
          <Text style={styles.muted}>General-audience experiences run through the verified local Agent OS. Stored output is the untouched model artifact.</Text>
          {creativeCapabilities !== null && <Text style={styles.detail}>Local text: ready · image and voice: runtime-dependent · video, animation, generative audio editing, and protected adult operation: external dependency</Text>}
          <ScrollView horizontal contentContainerStyle={styles.chipRow}>
            {creativeModes.map((mode) => <Pressable accessibilityRole="button" accessibilityState={{ selected: creativeMode === mode }} key={mode} style={[styles.chip, creativeMode === mode && styles.selectedChip]} onPress={() => setCreativeMode(mode)}><Text style={styles.chipText}>{mode}</Text></Pressable>)}
          </ScrollView>
          <TextInput accessibilityLabel="Creative experience title" maxLength={160} value={creativeTitle} onChangeText={setCreativeTitle} placeholder="Experience title" placeholderTextColor={colors.subtle} style={styles.input} />
          <TextInput accessibilityLabel="Creative experience premise" multiline maxLength={4_000} value={creativePremise} onChangeText={setCreativePremise} placeholder="Premise and setting" placeholderTextColor={colors.subtle} style={styles.textArea} />
          <TextInput accessibilityLabel="Creative experience genre" maxLength={80} value={creativeGenre} onChangeText={setCreativeGenre} placeholder="Genre" placeholderTextColor={colors.subtle} style={styles.input} />
          <TextInput accessibilityLabel="Creative experience language" autoCapitalize="none" maxLength={35} value={creativeLanguage} onChangeText={setCreativeLanguage} placeholder="Language tag, e.g. en" placeholderTextColor={colors.subtle} style={styles.input} />
          {creativeMode === "character" && <TextInput accessibilityLabel="Fictional character name" maxLength={120} value={creativeCharacter} onChangeText={setCreativeCharacter} placeholder="Fictional character name" placeholderTextColor={colors.subtle} style={styles.input} />}
          <Pressable accessibilityRole="button" disabled={busyAction !== null || creativeTitle.trim().length === 0 || creativePremise.trim().length === 0 || creativeGenre.trim().length === 0 || (creativeMode === "character" && creativeCharacter.trim().length === 0)} style={[styles.primaryButton, busyAction !== null && styles.disabled]} onPress={() => void createMobileCreativeExperience()}><Text style={styles.primaryButtonText}>Create experience</Text></Pressable>
          {creativeExperiences.length > 0 && <ScrollView horizontal contentContainerStyle={styles.chipRow}>
            {creativeExperiences.map((experience) => <Pressable accessibilityRole="button" accessibilityState={{ selected: selectedCreative?.id === experience.id }} key={experience.id} style={[styles.chip, selectedCreative?.id === experience.id && styles.selectedChip]} onPress={() => setSelectedCreativeId(experience.id)}><Text style={styles.chipText}>{experience.title}</Text></Pressable>)}
          </ScrollView>}
          {selectedCreative !== null && <>
            <View style={styles.result}><Text style={styles.itemLabel}>{selectedCreative.mode} · {selectedCreative.status} · {selectedCreative.turn_count}/100 turns</Text><Text style={styles.itemText}>{selectedCreative.premise}</Text></View>
            {selectedCreative.turns.map((turn) => <View key={turn.id} style={styles.result}><Text style={styles.itemLabel}>Turn {turn.position} · verified {turn.output_sha256.slice(0, 12)}</Text><Text selectable style={styles.detail}>You: {turn.owner_input}</Text><Text selectable style={styles.itemText}>AI: {turn.output}</Text><Text style={styles.detail}>Model {turn.model_id}</Text></View>)}
            {selectedCreative.status === "active" && <>
              <TextInput accessibilityLabel="Creative next move" multiline maxLength={4_000} value={creativeTurn} onChangeText={setCreativeTurn} placeholder="What happens next?" placeholderTextColor={colors.subtle} style={styles.textArea} />
              <View style={styles.buttonRow}><Pressable accessibilityRole="button" disabled={busyAction !== null || creativeTurn.trim().length === 0 || selectedCreative.turn_count >= 100} style={styles.primaryButton} onPress={() => void addMobileCreativeTurn()}><Text style={styles.primaryButtonText}>Generate verified turn</Text></Pressable><Pressable accessibilityRole="button" disabled={busyAction !== null || selectedCreative.turn_count === 0} style={styles.secondaryButton} onPress={() => void completeMobileCreativeExperience()}><Text style={styles.buttonText}>Complete</Text></Pressable></View>
            </>}
          </>}
          <Text style={styles.warning}>No local video, animation, generative audio editing, or protected adult capability is claimed. External policy/runtime checks remain required.</Text>
        </View>

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
              <Pressable accessibilityRole="button" style={styles.touchAction} onPress={() => void forgetMemory(memory.id)}>
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
                <Pressable accessibilityRole="button" style={styles.touchAction} onPress={() => void startWorkflow(workflow.id)}>
                  <Text style={styles.link}>Start</Text>
                </Pressable>
              )}
              {workflow.status === "running" && (
                <Pressable accessibilityRole="button" style={styles.touchAction} onPress={() => void cancelWorkflow(workflow.id)}>
                  <Text style={styles.danger}>Cancel</Text>
                </Pressable>
              )}
              <Pressable accessibilityRole="button" style={styles.touchAction} onPress={() => void refreshWorkflow(workflow.id)}>
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
    chip: { minHeight: 44, justifyContent: "center", borderColor: colors.line, borderWidth: 1, borderRadius: 999, paddingHorizontal: 12 },
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
    touchAction: { minHeight: 44, justifyContent: "center" },
    listItem: { flexDirection: "row", alignItems: "center", gap: 10, borderTopColor: colors.line, borderTopWidth: 1, paddingTop: 10 },
    featureRow: { flexDirection: "row", alignItems: "flex-start", gap: 10, borderTopColor: colors.line, borderTopWidth: 1, paddingTop: 10 },
    availability: { color: colors.accent, fontSize: 10, fontWeight: "900", textTransform: "uppercase" },
    itemLabel: { color: colors.accent, fontSize: 11, fontWeight: "900", textTransform: "uppercase" },
    itemText: { color: colors.text, lineHeight: 20 },
    result: { borderColor: colors.line, borderWidth: 1, borderRadius: 10, backgroundColor: colors.background, padding: 10, gap: 6 },
    codeText: { color: colors.text, fontFamily: "monospace", fontSize: 12, lineHeight: 18 },
    workflowItem: { minHeight: 54, flexDirection: "row", alignItems: "center", gap: 8, borderTopColor: colors.line, borderTopWidth: 1, paddingTop: 9 },
    generatedImage: { width: "100%", aspectRatio: 1, borderRadius: 12, backgroundColor: colors.background },
  });
}
