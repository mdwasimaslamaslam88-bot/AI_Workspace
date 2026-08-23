import type { ProductCapability } from "@work-station/shared";
import { useCallback, useEffect, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { useWorkStation } from "@/context/work-station";
import { requestPrivateNotificationPermission } from "@/notifications/private-notifications";

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
  const { state, client, user, logout, retry } = useWorkStation();
  const [capabilities, setCapabilities] = useState<ProductCapability[]>([]);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (client === null || state !== "connected") return;
    try {
      setCapabilities((await client.getCapabilities()).items);
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

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: "#040c1f" },
  content: { padding: 16, gap: 14, paddingBottom: 40 },
  statusCard: { borderColor: "#1d6d62", borderWidth: 1, borderRadius: 16, backgroundColor: "#0a282e", padding: 16, gap: 9 },
  card: { borderColor: "#263b58", borderWidth: 1, borderRadius: 16, backgroundColor: "#0e1c33", padding: 16, gap: 10 },
  eyebrow: { color: "#68efc8", fontSize: 11, fontWeight: "900", letterSpacing: 1.4 },
  heading: { color: "#e8edf4", fontSize: 20, fontWeight: "900", textTransform: "capitalize" },
  muted: { color: "#9ba9ba", lineHeight: 20 },
  capability: { flexDirection: "row", justifyContent: "space-between", gap: 12, borderTopColor: "#263b58", borderTopWidth: 1, paddingTop: 9 },
  capabilityName: { flex: 1, color: "#e8edf4", textTransform: "capitalize" },
  available: { color: "#68efc8", fontWeight: "800" },
  unavailable: { color: "#ffb4ab", fontWeight: "800" },
  error: { color: "#ffb4ab" },
  secondaryButton: { alignSelf: "flex-start", minHeight: 44, justifyContent: "center", borderColor: "#263b58", borderWidth: 1, borderRadius: 10, paddingHorizontal: 14 },
  buttonText: { color: "#e8edf4", fontWeight: "800" },
  sectionGrid: { flexDirection: "row", flexWrap: "wrap", gap: 7 },
  sectionChip: { color: "#c8d3e1", backgroundColor: "#14243c", borderRadius: 999, paddingHorizontal: 10, paddingVertical: 7, fontSize: 12 },
  safety: { color: "#718199", fontSize: 12, lineHeight: 18 },
  logoutButton: { minHeight: 48, justifyContent: "center", alignItems: "center", borderColor: "#ffb4ab", borderWidth: 1, borderRadius: 12 },
  logoutText: { color: "#ffb4ab", fontWeight: "900" },
});
