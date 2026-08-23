import type { ProductCapability, SystemDiagnostics } from "@work-station/shared";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, useColorScheme, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

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
  const scheme = useColorScheme();
  const styles = useMemo(() => createStyles(workStationColors(scheme)), [scheme]);
  const { state, client, user, logout, retry, rotateSession } = useWorkStation();
  const [capabilities, setCapabilities] = useState<ProductCapability[]>([]);
  const [diagnostics, setDiagnostics] = useState<SystemDiagnostics | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [sessionBusy, setSessionBusy] = useState(false);

  const load = useCallback(async () => {
    if (client === null || state !== "connected") return;
    try {
      const [capabilityPage, snapshot] = await Promise.all([
        client.getCapabilities(),
        client.getSystemDiagnostics(),
      ]);
      setCapabilities(capabilityPage.items);
      setDiagnostics(snapshot);
      setNotice(null);
    } catch {
      setNotice("Private diagnostics could not be refreshed.");
    }
  }, [client, state]);

  useEffect(() => {
    const timer = setTimeout(() => void load(), 0);
    return () => clearTimeout(timer);
  }, [load]);

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
          <Pressable style={styles.secondaryButton} onPress={() => void retry()}>
            <Text style={styles.buttonText}>Refresh connection</Text>
          </Pressable>
          <Pressable
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
          <Pressable style={styles.secondaryButton} onPress={() => void load()}>
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

        <Pressable style={styles.logoutButton} onPress={() => void logout()}>
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
  available: { color: colors.accent, fontWeight: "800" },
  unavailable: { color: colors.danger, fontWeight: "800" },
  error: { color: colors.danger },
  secondaryButton: { alignSelf: "flex-start", minHeight: 44, justifyContent: "center", borderColor: colors.line, borderWidth: 1, borderRadius: 10, paddingHorizontal: 14 },
  buttonText: { color: colors.text, fontWeight: "800" },
  sectionGrid: { flexDirection: "row", flexWrap: "wrap", gap: 7 },
  sectionChip: { color: colors.text, backgroundColor: colors.soft, borderRadius: 999, paddingHorizontal: 10, paddingVertical: 7, fontSize: 12 },
  safety: { color: colors.subtle, fontSize: 12, lineHeight: 18 },
  logoutButton: { minHeight: 48, justifyContent: "center", alignItems: "center", borderColor: colors.danger, borderWidth: 1, borderRadius: 12 },
  logoutText: { color: colors.danger, fontWeight: "900" },
  });
}
