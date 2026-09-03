import type {
  CommunicationAccepted,
  CommunicationCapabilities,
  CommunicationRequest,
  Connector,
} from "@work-station/shared";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { MobileApiError } from "@/api/client";
import { useWorkStation } from "@/context/work-station";
import { useWorkStationAppearance } from "@/theme/appearance";
import type { WorkStationColors } from "@/theme/colors";

type Operation = "phone_call" | "callback";

function safeError(cause: unknown): string {
  return cause instanceof MobileApiError
    ? cause.message
    : "The provider did not return a verified acceptance receipt.";
}

export default function CallsScreen() {
  const { colors } = useWorkStationAppearance();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const { state, client } = useWorkStation();
  const [capabilities, setCapabilities] = useState<CommunicationCapabilities | null>(null);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [operation, setOperation] = useState<Operation>("phone_call");
  const [connectorId, setConnectorId] = useState("");
  const [destination, setDestination] = useState("");
  const [purpose, setPurpose] = useState("");
  const [approved, setApproved] = useState(false);
  const [receipt, setReceipt] = useState<CommunicationAccepted | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (signal?: AbortSignal) => {
    if (client === null || state !== "connected") return;
    setLoading(true);
    setNotice(null);
    try {
      const [nextCapabilities, connectorPage] = await Promise.all([
        client.getCommunicationCapabilities(signal),
        client.listConnectors(signal),
      ]);
      if (signal?.aborted) return;
      setCapabilities(nextCapabilities);
      setConnectors(connectorPage.items);
    } catch (cause) {
      if (!signal?.aborted) setNotice(safeError(cause));
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [client, state]);

  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => void load(controller.signal), 0);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [load]);

  const capability = capabilities?.[operation] ?? null;
  const eligible = useMemo(() => {
    const ids = new Set(capability?.connector_ids ?? []);
    return connectors.filter((connector) => ids.has(connector.id));
  }, [capability?.connector_ids, connectors]);

  const selectedConnectorId = eligible.some((item) => item.id === connectorId)
    ? connectorId
    : eligible[0]?.id ?? "";

  const canSubmit = Boolean(
    !busy && capability?.configured && selectedConnectorId &&
    /^\+[1-9][0-9]{7,14}$/.test(destination) &&
    purpose.trim().length > 0 && purpose.trim().length <= 240 && approved,
  );

  async function submit() {
    if (!canSubmit || client === null) return;
    const request: CommunicationRequest = {
      destination,
      purpose: purpose.trim(),
      owner_approved: true,
      connector_id: selectedConnectorId,
    };
    setBusy(true);
    setNotice(null);
    setReceipt(null);
    try {
      const accepted = operation === "phone_call"
        ? await client.startPhoneCall(request)
        : await client.scheduleCallback(request);
      setReceipt(accepted);
      setApproved(false);
      setNotice("Provider acceptance receipt verified and connector execution audited.");
    } catch (cause) {
      setNotice(safeError(cause));
    } finally {
      setBusy(false);
    }
  }

  if (state !== "connected" || client === null) {
    return (
      <SafeAreaView edges={["left", "right"]} style={styles.safe}>
        <View style={styles.centered}>
          <Text accessibilityRole="header" style={styles.heading}>Communications unavailable</Text>
          <Text style={styles.muted}>Connect an authenticated owner session from Home first.</Text>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView edges={["left", "right"]} style={styles.safe}>
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <View style={styles.card}>
          <Text style={styles.eyebrow}>GLOBAL COMMUNICATIONS</Text>
          <Text accessibilityRole="header" style={styles.heading}>Calls & callbacks</Text>
          <Text style={styles.muted}>
            AI OS reports acceptance only after the owner-scoped provider returns a matching receipt.
          </Text>
          {loading && <ActivityIndicator color={colors.accent} accessibilityLabel="Checking communication providers" />}
          <View style={styles.row}>
            {(["phone_call", "callback"] as const).map((item) => (
              <Pressable
                key={item}
                accessibilityRole="button"
                accessibilityState={{ selected: operation === item }}
                style={[styles.chip, operation === item && styles.selectedChip]}
                onPress={() => {
                  setOperation(item);
                  setReceipt(null);
                }}
              >
                <Text style={styles.buttonText}>{item === "phone_call" ? "Phone call" : "Callback"}</Text>
              </Pressable>
            ))}
          </View>
          <View style={styles.section}>
              <Text style={styles.label}>Verified gateway</Text>
              {eligible.length === 0 ? (
                <Text style={styles.warning}>
                  No healthy communication connector. Register credentials and the exact provider origin in the protected desktop/web Connections panel, then run Health and Discover.
                </Text>
              ) : eligible.map((connector) => (
                <Pressable
                  key={connector.id}
                  accessibilityRole="button"
                  accessibilityState={{ selected: selectedConnectorId === connector.id }}
                  style={[styles.connector, selectedConnectorId === connector.id && styles.selectedConnector]}
                  onPress={() => {
                    setConnectorId(connector.id);
                    setReceipt(null);
                  }}
                >
                  <Text style={styles.label}>{connector.name}</Text>
                  <Text style={styles.muted}>{connector.provider} · {connector.connection_status}</Text>
                </Pressable>
              ))}
          </View>
          <TextInput
            accessibilityLabel="Call destination in E.164 format"
            keyboardType="phone-pad"
            autoComplete="tel"
            maxLength={16}
            placeholder="+14155550123"
            placeholderTextColor={colors.subtle}
            value={destination}
            onChangeText={setDestination}
            style={styles.input}
          />
          <TextInput
            accessibilityLabel="Communication purpose"
            multiline
            maxLength={240}
            placeholder="What should the AI accomplish during this communication?"
            placeholderTextColor={colors.subtle}
            value={purpose}
            onChangeText={setPurpose}
            style={styles.textArea}
          />
          <Pressable
            accessibilityRole="checkbox"
            accessibilityState={{ checked: approved }}
            style={styles.approval}
            onPress={() => setApproved((current) => !current)}
          >
            <View style={[styles.checkbox, approved && styles.checkboxChecked]} />
            <Text style={styles.approvalText}>I approve this external communication and its provider charges.</Text>
          </Pressable>
          <Pressable
            accessibilityRole="button"
            disabled={!canSubmit}
            style={[styles.primaryButton, !canSubmit && styles.disabled]}
            onPress={() => void submit()}
          >
            <Text style={styles.primaryButtonText}>
              {busy ? "Waiting for provider…" : operation === "phone_call" ? "Place verified call" : "Schedule verified callback"}
            </Text>
          </Pressable>
          {notice !== null && <Text accessibilityRole="alert" style={styles.notice}>{notice}</Text>}
          {receipt !== null && (
            <View style={styles.receipt}>
              <Text style={styles.label}>Provider state: {receipt.state.replaceAll("_", " ")}</Text>
              <Text selectable style={styles.code}>Request: {receipt.request_id}</Text>
              <Text selectable style={styles.code}>Audit: {receipt.connector_execution_id}</Text>
            </View>
          )}
        </View>
        <View style={styles.card}>
          <Text style={styles.eyebrow}>EXTERNAL BOUNDARIES</Text>
          <Text style={styles.muted}>Carrier account, phone number, billing, provider authentication, and any MFA remain owner/provider controlled.</Text>
          <Text style={styles.muted}>Video calling and live screen sharing require a separately verified WebRTC provider.</Text>
          <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => void load()}>
            <Text style={styles.buttonText}>Refresh provider status</Text>
          </Pressable>
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

function createStyles(colors: WorkStationColors) {
  return StyleSheet.create({
    safe: { flex: 1, backgroundColor: colors.background },
    content: { padding: 16, gap: 14, paddingBottom: 40 },
    centered: { flex: 1, justifyContent: "center", alignItems: "center", padding: 24, gap: 10 },
    card: { backgroundColor: colors.raised, borderColor: colors.line, borderWidth: 1, borderRadius: 16, padding: 16, gap: 12 },
    eyebrow: { color: colors.accent, fontSize: 11, fontWeight: "900", letterSpacing: 1.4 },
    heading: { color: colors.text, fontSize: 22, fontWeight: "900" },
    muted: { color: colors.muted, lineHeight: 20 },
    warning: { color: colors.danger, lineHeight: 20 },
    row: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
    chip: { minHeight: 44, justifyContent: "center", borderColor: colors.line, borderWidth: 1, borderRadius: 999, paddingHorizontal: 14 },
    selectedChip: { borderColor: colors.accent, backgroundColor: colors.accentSoft },
    section: { gap: 8 },
    label: { color: colors.text, fontWeight: "800" },
    connector: { borderColor: colors.line, borderWidth: 1, borderRadius: 12, padding: 12, gap: 3 },
    selectedConnector: { borderColor: colors.accent, backgroundColor: colors.accentSoft },
    input: { minHeight: 48, borderColor: colors.line, borderWidth: 1, borderRadius: 10, paddingHorizontal: 12, color: colors.text, backgroundColor: colors.soft },
    textArea: { minHeight: 96, textAlignVertical: "top", borderColor: colors.line, borderWidth: 1, borderRadius: 10, padding: 12, color: colors.text, backgroundColor: colors.soft },
    approval: { minHeight: 48, flexDirection: "row", alignItems: "center", gap: 10 },
    checkbox: { width: 22, height: 22, borderWidth: 2, borderColor: colors.line, borderRadius: 5 },
    checkboxChecked: { borderColor: colors.accent, backgroundColor: colors.accent },
    approvalText: { flex: 1, color: colors.text, lineHeight: 20 },
    primaryButton: { minHeight: 48, alignItems: "center", justifyContent: "center", borderRadius: 12, backgroundColor: colors.accent },
    primaryButtonText: { color: colors.onAccent, fontWeight: "900" },
    secondaryButton: { minHeight: 44, alignSelf: "flex-start", justifyContent: "center", borderColor: colors.line, borderWidth: 1, borderRadius: 10, paddingHorizontal: 14 },
    buttonText: { color: colors.text, fontWeight: "800" },
    disabled: { opacity: 0.45 },
    notice: { color: colors.text, lineHeight: 20 },
    receipt: { backgroundColor: colors.soft, borderRadius: 12, padding: 12, gap: 6 },
    code: { color: colors.muted, fontFamily: "monospace", fontSize: 11 },
  });
}
