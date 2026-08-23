import type {
  LocalModel,
  ProductCapability,
  SystemDiagnostics,
  UserSession,
  UserSessionProvision,
} from "@work-station/shared";
import {
  MODEL_READINESS_LABELS,
  modelContextLabel,
  modelHardwareLabel,
  modelReadiness,
  modelScaleLabel,
} from "@work-station/shared";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AppState,
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
import * as ScreenCapture from "expo-screen-capture";

import { shouldClearTransientSession } from "@/auth/transient-session";
import { useWorkStation } from "@/context/work-station";
import { requestPrivateNotificationPermission } from "@/notifications/private-notifications";
import { workStationColors, type WorkStationColors } from "@/theme/colors";

const SECTIONS = [
  "Account",
  "Sessions",
  "Appearance",
  "Model",
  "AI capabilities",
  "Memory",
  "Voice",
  "Storage",
  "Notifications",
  "Connection",
  "Diagnostics",
  "Security",
];

export default function SettingsScreen() {
  ScreenCapture.usePreventScreenCapture("work-station-owner-settings");
  const scheme = useColorScheme();
  const styles = useMemo(() => createStyles(workStationColors(scheme)), [scheme]);
  const { state, client, user, logout, retry, rotateSession } = useWorkStation();
  const [capabilities, setCapabilities] = useState<ProductCapability[]>([]);
  const [models, setModels] = useState<LocalModel[]>([]);
  const [diagnostics, setDiagnostics] = useState<SystemDiagnostics | null>(null);
  const [sessions, setSessions] = useState<UserSession[]>([]);
  const [currentSessionLabel, setCurrentSessionLabel] = useState("");
  const [newSessionLabel, setNewSessionLabel] = useState("");
  const [issuedSession, setIssuedSession] = useState<UserSessionProvision | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [sessionBusy, setSessionBusy] = useState(false);

  const load = useCallback(async () => {
    if (client === null || state !== "connected") return;
    try {
      const [capabilityPage, modelPage, snapshot, sessionPage] = await Promise.all([
        client.getCapabilities(),
        client.listModels(),
        client.getSystemDiagnostics(),
        client.listUserSessions(),
      ]);
      setCapabilities(capabilityPage.items);
      setModels(modelPage.items);
      setDiagnostics(snapshot);
      setSessions(sessionPage.items);
      setCurrentSessionLabel(
        sessionPage.items.find((item) => item.is_current)?.label ?? "",
      );
      setNotice(null);
    } catch {
      setNotice("Private diagnostics could not be refreshed.");
    }
  }, [client, state]);

  useEffect(() => {
    const timer = setTimeout(() => void load(), 0);
    return () => clearTimeout(timer);
  }, [load]);

  useEffect(() => {
    const subscription = AppState.addEventListener("change", (visibility) => {
      if (shouldClearTransientSession(visibility)) setIssuedSession(null);
    });
    if (Platform.OS === "ios") {
      void ScreenCapture.enableAppSwitcherProtectionAsync(1).catch(() => undefined);
    }
    return () => {
      subscription.remove();
      if (Platform.OS === "ios") {
        void ScreenCapture.disableAppSwitcherProtectionAsync().catch(() => undefined);
      }
    };
  }, []);

  return (
    <SafeAreaView edges={["left", "right"]} style={styles.safe}>
      <ScrollView contentContainerStyle={styles.content}>
        <View style={styles.statusCard}>
          <Text style={styles.eyebrow}>CONNECTION</Text>
          <Text style={styles.heading}>{state.replaceAll("_", " ")}</Text>
          <Text style={styles.muted}>
            Mode: {process.env.EXPO_PUBLIC_API_BASE_URL?.startsWith("https://") ? "REMOTE" : "LOCAL"}
          </Text>
          {user !== null && <Text style={styles.muted}>Owner session active</Text>}
          <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => void retry()}>
            <Text style={styles.buttonText}>Refresh connection</Text>
          </Pressable>
          <Pressable
            accessibilityRole="button"
            disabled={sessionBusy}
            style={styles.secondaryButton}
            onPress={() => {
              setSessionBusy(true);
              setNotice(null);
              void rotateSession()
                .then(() => setNotice("Owner access token rotated on this device."))
                .catch(() =>
                  setNotice("The session could not be rotated safely. Keep the app open and retry."),
                )
                .finally(() => setSessionBusy(false));
            }}
          >
            <Text style={styles.buttonText}>
              {sessionBusy ? "Rotating…" : "Rotate owner token"}
            </Text>
          </Pressable>
        </View>

        <View style={styles.card}>
          <Text style={styles.eyebrow}>MODEL CATALOG</Text>
          <Text style={styles.heading}>Hardware-aware models</Text>
          <Text style={styles.muted}>
            Availability comes from the workstation. This app never downloads models automatically.
          </Text>
          {models.length === 0 && <Text style={styles.muted}>No models were reported.</Text>}
          {models.map((model) => {
            const readiness = modelReadiness(model);
            return (
              <View key={model.model_id} style={styles.modelCard}>
                <View style={styles.capability}>
                  <Text style={styles.capabilityName}>{model.display_name}</Text>
                  <Text style={readiness === "ready" ? styles.available : styles.unavailable}>
                    {MODEL_READINESS_LABELS[readiness]}
                  </Text>
                </View>
                <Text style={styles.muted}>
                  {model.installed ? "Installed" : "Not installed"} · {model.runtime_id} · {model.modality}
                </Text>
                <Text style={styles.muted}>
                  {modelScaleLabel(model.scale_class)} · {modelContextLabel(model.context_window)}
                </Text>
                <Text style={styles.muted}>{modelHardwareLabel(model.hardware_class)}</Text>
                <View style={styles.capabilityTags}>
                  {model.capabilities.map((capability) => (
                    <Text key={capability} style={styles.sectionChip}>
                      {capability.replaceAll("_", " ")}
                    </Text>
                  ))}
                </View>
              </View>
            );
          })}
        </View>

        <View style={styles.card}>
          <Text style={styles.eyebrow}>OWNER SESSIONS</Text>
          <Text style={styles.heading}>Active devices</Text>
          <Text style={styles.muted}>
            Each device credential can be named and revoked independently. Tokens are never listed.
          </Text>
          <TextInput
            accessibilityLabel="This device label"
            autoCapitalize="sentences"
            maxLength={80}
            placeholder="This phone"
            style={styles.input}
            value={currentSessionLabel}
            onChangeText={setCurrentSessionLabel}
          />
          <Pressable
            accessibilityRole="button"
            disabled={sessionBusy}
            style={styles.secondaryButton}
            onPress={() => {
              if (client === null) return;
              setSessionBusy(true);
              setNotice(null);
              void client.renameCurrentUserSession({ label: currentSessionLabel.trim() || null })
                .then((renamed) => {
                  setSessions((items) => items.map((item) => item.id === renamed.id ? renamed : item));
                  setNotice("Device label updated.");
                })
                .catch(() => setNotice("The device label could not be updated."))
                .finally(() => setSessionBusy(false));
            }}
          >
            <Text style={styles.buttonText}>Save this device label</Text>
          </Pressable>
          <TextInput
            accessibilityLabel="New device label"
            autoCapitalize="sentences"
            maxLength={80}
            placeholder="Tablet or laptop"
            style={styles.input}
            value={newSessionLabel}
            onChangeText={setNewSessionLabel}
          />
          <Pressable
            accessibilityRole="button"
            disabled={sessionBusy}
            style={styles.secondaryButton}
            onPress={() => {
              if (client === null) return;
              setSessionBusy(true);
              setNotice(null);
              setIssuedSession(null);
              void client.createUserSession({ label: newSessionLabel.trim() || null })
                .then((created) => {
                  setSessions((items) => [created.session, ...items]);
                  setIssuedSession(created);
                  setNewSessionLabel("");
                })
                .catch(() => setNotice("A new device session could not be created."))
                .finally(() => setSessionBusy(false));
            }}
          >
            <Text style={styles.buttonText}>Issue device token</Text>
          </Pressable>
          {issuedSession !== null && (
            <View style={styles.issuedSession}>
              <Text style={styles.capabilityName}>
                Copy this token now. It will not be shown again.
              </Text>
              <Text style={styles.safety}>
                This one-time view is screen-capture protected and clears when the app leaves the foreground.
              </Text>
              <Text selectable style={styles.token}>{issuedSession.access_token}</Text>
              <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => setIssuedSession(null)}>
                <Text style={styles.buttonText}>I saved it</Text>
              </Pressable>
            </View>
          )}
          {sessions.map((accessSession) => (
            <View key={accessSession.id} style={styles.sessionRow}>
              <View style={styles.sessionDetail}>
                <Text style={styles.capabilityName}>
                  {accessSession.label ?? "Unnamed device"}
                </Text>
                <Text style={styles.muted}>
                  {accessSession.is_current ? "Current device" : "Active device"}
                </Text>
              </View>
              {!accessSession.is_current && (
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel={`Revoke ${accessSession.label ?? "unnamed device"}`}
                  disabled={sessionBusy}
                  style={styles.revokeButton}
                  onPress={() => {
                    if (client === null) return;
                    setSessionBusy(true);
                    setNotice(null);
                    void client.revokeUserSession(accessSession.id)
                      .then(() => {
                        setSessions((items) => items.filter((item) => item.id !== accessSession.id));
                        setNotice("Device session revoked.");
                      })
                      .catch(() => setNotice("The device session could not be revoked."))
                      .finally(() => setSessionBusy(false));
                  }}
                >
                  <Text style={styles.logoutText}>Revoke</Text>
                </Pressable>
              )}
            </View>
          ))}
        </View>

        <View style={styles.card}>
          <Text style={styles.eyebrow}>PRIVATE DIAGNOSTICS</Text>
          <Text style={styles.heading}>Capabilities</Text>
          {capabilities.map((capability) => (
            <View key={capability.id} style={styles.capability}>
              <Text style={styles.capabilityName}>{capability.id.replaceAll("_", " ")}</Text>
              <Text style={capability.status === "available" ? styles.available : styles.unavailable}>
                {capability.status}
              </Text>
            </View>
          ))}
          {diagnostics !== null && (
            <>
              <Text style={styles.muted}>Mode: {diagnostics.mode.toUpperCase()}</Text>
              {diagnostics.services.map((service) => (
                <View key={service.id} style={styles.capability}>
                  <Text style={styles.capabilityName}>
                    {service.id.replaceAll("_", " ")}
                  </Text>
                  <Text
                    style={
                      service.status === "ready"
                        ? styles.available
                        : styles.unavailable
                    }
                  >
                    {service.status}
                  </Text>
                </View>
              ))}
              {diagnostics.gpus.map((gpu) => (
                <Text key={`${gpu.model}-${gpu.vram_bytes}`} style={styles.muted}>
                  {gpu.model} · {Math.round(gpu.vram_bytes / 1024 ** 3)} GiB
                </Text>
              ))}
            </>
          )}
          {notice !== null && <Text accessibilityRole="alert" style={styles.error}>{notice}</Text>}
          <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => void load()}>
            <Text style={styles.buttonText}>Refresh diagnostics</Text>
          </Pressable>
        </View>

        <View style={styles.card}>
          <Text style={styles.eyebrow}>NOTIFICATIONS</Text>
          <Text style={styles.heading}>Private previews</Text>
          <Text style={styles.muted}>
            Completion alerts omit prompts, responses, conversation names, and filenames by default.
          </Text>
          <Pressable
            accessibilityRole="button"
            style={styles.secondaryButton}
            onPress={() => void requestPrivateNotificationPermission().then((granted) =>
              setNotice(granted ? "Notifications enabled." : "Notification permission was not granted."),
            )}
          >
            <Text style={styles.buttonText}>Configure notifications</Text>
          </Pressable>
        </View>

        <View style={styles.card}>
          <Text style={styles.eyebrow}>SETTINGS MAP</Text>
          <View style={styles.sectionGrid}>
            {SECTIONS.map((section) => <Text key={section} style={styles.sectionChip}>{section}</Text>)}
          </View>
          <Text style={styles.safety}>
            Credentials, runtime URLs, raw filesystem paths, and private content are never shown here.
          </Text>
        </View>

        <Pressable accessibilityRole="button" style={styles.logoutButton} onPress={() => void logout()}>
          <Text style={styles.logoutText}>Log out on this device</Text>
        </Pressable>
      </ScrollView>
    </SafeAreaView>
  );
}

function createStyles(colors: WorkStationColors) {
  return StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  content: { padding: 16, gap: 14, paddingBottom: 40 },
  statusCard: { borderColor: colors.accentBorder, borderWidth: 1, borderRadius: 16, backgroundColor: colors.accentSoft, padding: 16, gap: 9 },
  card: { borderColor: colors.line, borderWidth: 1, borderRadius: 16, backgroundColor: colors.raised, padding: 16, gap: 10 },
  eyebrow: { color: colors.accent, fontSize: 11, fontWeight: "900", letterSpacing: 1.4 },
  heading: { color: colors.text, fontSize: 20, fontWeight: "900", textTransform: "capitalize" },
  muted: { color: colors.muted, lineHeight: 20 },
  capability: { flexDirection: "row", justifyContent: "space-between", gap: 12, borderTopColor: colors.line, borderTopWidth: 1, paddingTop: 9 },
  capabilityName: { flex: 1, color: colors.text, textTransform: "capitalize" },
  modelCard: { gap: 7, borderTopColor: colors.line, borderTopWidth: 1, paddingTop: 10 },
  capabilityTags: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  available: { color: colors.accent, fontWeight: "800" },
  unavailable: { color: colors.danger, fontWeight: "800" },
  error: { color: colors.danger },
  secondaryButton: { alignSelf: "flex-start", minHeight: 44, justifyContent: "center", borderColor: colors.line, borderWidth: 1, borderRadius: 10, paddingHorizontal: 14 },
  input: { minHeight: 48, borderColor: colors.line, borderWidth: 1, borderRadius: 10, paddingHorizontal: 12, color: colors.text, backgroundColor: colors.soft },
  issuedSession: { gap: 9, borderColor: colors.accentBorder, borderWidth: 1, borderRadius: 12, backgroundColor: colors.accentSoft, padding: 12 },
  token: { color: colors.text, fontFamily: "monospace", fontSize: 12 },
  sessionRow: { minHeight: 52, flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12, borderTopColor: colors.line, borderTopWidth: 1, paddingTop: 9 },
  sessionDetail: { flex: 1, gap: 2 },
  revokeButton: { minHeight: 44, justifyContent: "center", paddingHorizontal: 10 },
  buttonText: { color: colors.text, fontWeight: "800" },
  sectionGrid: { flexDirection: "row", flexWrap: "wrap", gap: 7 },
  sectionChip: { color: colors.text, backgroundColor: colors.soft, borderRadius: 999, paddingHorizontal: 10, paddingVertical: 7, fontSize: 12 },
  safety: { color: colors.subtle, fontSize: 12, lineHeight: 18 },
  logoutButton: { minHeight: 48, justifyContent: "center", alignItems: "center", borderColor: colors.danger, borderWidth: 1, borderRadius: 12 },
  logoutText: { color: colors.danger, fontWeight: "900" },
  });
}
