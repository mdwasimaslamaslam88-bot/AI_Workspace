import type { AgentOSCapabilities, AgentRun, ModelTask } from "@work-station/shared";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  useColorScheme,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { useWorkStation } from "@/context/work-station";
import { workStationColors, type WorkStationColors } from "@/theme/colors";


const TASKS: ModelTask[] = [
  "general_chat",
  "reasoning",
  "mathematics",
  "coding",
  "code_generation",
  "debugging",
  "expert_analysis",
  "vision",
  "rag",
  "workflow_planning",
];
const TERMINAL = new Set(["completed", "failed", "cancelled", "timed_out"]);

export default function AgentsScreen() {
  const scheme = useColorScheme();
  const styles = useMemo(() => createStyles(workStationColors(scheme)), [scheme]);
  const { state, client } = useWorkStation();
  const [capabilities, setCapabilities] = useState<AgentOSCapabilities | null>(null);
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [goal, setGoal] = useState("");
  const [task, setTask] = useState<ModelTask>("general_chat");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (client === null || state !== "connected") return;
    try {
      const [profiles, page] = await Promise.all([
        client.getAgentOSCapabilities(),
        client.listAgentRuns(),
      ]);
      setCapabilities(profiles);
      setRuns(page.items);
      setNotice(null);
    } catch {
      setNotice("Agent status could not be refreshed.");
    }
  }, [client, state]);

  useEffect(() => {
    const timer = setTimeout(() => void load(), 0);
    return () => clearTimeout(timer);
  }, [load]);

  useEffect(() => {
    if (!runs.some((run) => !TERMINAL.has(run.status))) return;
    const timer = setInterval(() => void load(), 1500);
    return () => clearInterval(timer);
  }, [load, runs]);

  return (
    <SafeAreaView edges={["left", "right"]} style={styles.safe}>
      <ScrollView contentContainerStyle={styles.content}>
        <View style={styles.card}>
          <Text style={styles.eyebrow}>AGENT OS</Text>
          <Text style={styles.heading}>Bounded specialist run</Text>
          <Text style={styles.muted}>
            Plan → local-first route → execute → independent verification → bounded retry.
          </Text>
          <TextInput
            accessibilityLabel="Agent goal"
            multiline
            maxLength={32_000}
            placeholder="Describe the goal"
            style={styles.input}
            value={goal}
            onChangeText={setGoal}
          />
          <Pressable
            accessibilityRole="button"
            style={styles.secondaryButton}
            onPress={() => setTask(TASKS[(TASKS.indexOf(task) + 1) % TASKS.length] ?? "general_chat")}
          >
            <Text style={styles.buttonText}>Task: {task.replaceAll("_", " ")}</Text>
          </Pressable>
          <Pressable
            accessibilityRole="button"
            disabled={busy}
            style={styles.primaryButton}
            onPress={() => {
              if (client === null || !goal.trim() || goal !== goal.trim()) {
                setNotice("Enter an exact nonblank goal without surrounding whitespace.");
                return;
              }
              setBusy(true);
              setNotice(null);
              void client.createAgentRun({ goal, task, max_retries: 1, deadline_seconds: 180 })
                .then((created) => {
                  setRuns((current) => [created, ...current.filter((run) => run.id !== created.id)]);
                  setGoal("");
                  setNotice("Agent run submitted with model-inference permission only.");
                })
                .catch(() => setNotice("The bounded agent run could not be submitted."))
                .finally(() => setBusy(false));
            }}
          >
            <Text style={styles.buttonText}>Run agent</Text>
          </Pressable>
          <Text style={styles.muted}>
            {capabilities === null
              ? "Loading profiles…"
              : `${capabilities.active_runs} active · ${capabilities.max_concurrency} maximum concurrent`}
          </Text>
          {notice !== null && <Text accessibilityRole="alert" style={styles.notice}>{notice}</Text>}
        </View>

        <View style={styles.card}>
          <View style={styles.row}>
            <Text style={styles.heading}>Owner runs</Text>
            <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => void load()}>
              <Text style={styles.buttonText}>Refresh</Text>
            </Pressable>
          </View>
          {runs.length === 0 && <Text style={styles.muted}>No agent runs retained.</Text>}
          {runs.map((run) => (
            <View key={run.id} style={styles.run}>
              <View style={styles.row}>
                <Text style={styles.runTitle}>{(run.specialist ?? run.task).replaceAll("_", " ")}</Text>
                <Text style={run.status === "completed" ? styles.ready : styles.status}>{run.status.replaceAll("_", " ")}</Text>
              </View>
              {run.output !== null && <Text style={styles.output}>{run.output}</Text>}
              {run.attempts.map((attempt) => (
                <Text key={`${attempt.step_id}-${attempt.attempt}`} style={styles.muted}>
                  Attempt {attempt.attempt} · {attempt.verified ? "verified" : "verification failed"}
                </Text>
              ))}
              {!TERMINAL.has(run.status) && (
                <Pressable
                  accessibilityRole="button"
                  style={styles.secondaryButton}
                  onPress={() => {
                    if (client === null) return;
                    void client.cancelAgentRun(run.id)
                      .then((cancelled) => setRuns((current) => current.map((item) => item.id === cancelled.id ? cancelled : item)))
                      .catch(() => setNotice("The agent run could not be cancelled."));
                  }}
                >
                  <Text style={styles.buttonText}>Cancel run</Text>
                </Pressable>
              )}
            </View>
          ))}
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

function createStyles(colors: WorkStationColors) {
  return StyleSheet.create({
    safe: { flex: 1, backgroundColor: colors.background },
    content: { padding: 16, gap: 14, paddingBottom: 40 },
    card: { borderColor: colors.line, borderWidth: 1, borderRadius: 16, backgroundColor: colors.raised, padding: 16, gap: 10 },
    eyebrow: { color: colors.accent, fontSize: 11, fontWeight: "900", letterSpacing: 1.4 },
    heading: { color: colors.text, fontSize: 20, fontWeight: "900" },
    muted: { color: colors.muted, lineHeight: 20 },
    input: { minHeight: 110, borderColor: colors.line, borderWidth: 1, borderRadius: 10, padding: 12, color: colors.text, backgroundColor: colors.soft, textAlignVertical: "top" },
    primaryButton: { minHeight: 48, justifyContent: "center", alignItems: "center", borderRadius: 10, backgroundColor: colors.accentSoft, borderColor: colors.accentBorder, borderWidth: 1 },
    secondaryButton: { alignSelf: "flex-start", minHeight: 44, justifyContent: "center", borderColor: colors.line, borderWidth: 1, borderRadius: 10, paddingHorizontal: 14 },
    buttonText: { color: colors.text, fontWeight: "800" },
    notice: { color: colors.text },
    row: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 10 },
    run: { gap: 8, borderTopColor: colors.line, borderTopWidth: 1, paddingTop: 10 },
    runTitle: { flex: 1, color: colors.text, fontWeight: "800", textTransform: "capitalize" },
    status: { color: colors.muted, textTransform: "capitalize" },
    ready: { color: colors.accent, fontWeight: "800" },
    output: { color: colors.text, lineHeight: 21 },
  });
}
