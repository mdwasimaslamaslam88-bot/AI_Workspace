import { useMemo, useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { copyPrivateMessageContent } from "@/chat/clipboard";
import { parseMobileInlineMarkdown, parseMobileMarkdown } from "@/chat/markdown";
import { useWorkStationAppearance } from "@/theme/appearance";
import type { WorkStationColors } from "@/theme/colors";

export function MobileMessageContent({ content }: { content: string }) {
  const { colors } = useWorkStationAppearance();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const blocks = useMemo(() => parseMobileMarkdown(content), [content]);
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");

  async function copy() {
    try {
      await copyPrivateMessageContent(content);
      setCopyState("copied");
    } catch {
      setCopyState("failed");
    }
  }

  const inline = (text: string) => (
    <Text selectable style={styles.body}>
      {parseMobileInlineMarkdown(text).map((span, index) => (
        <Text
          key={`${index}:${span.kind}`}
          style={
            span.kind === "strong"
              ? styles.strong
              : span.kind === "emphasis"
                ? styles.emphasis
                : span.kind === "code"
                  ? styles.inlineCode
                  : undefined
          }
        >
          {span.text}
        </Text>
      ))}
    </Text>
  );

  return (
    <View style={styles.container}>
      {blocks.map((block, index) => {
        const key = `${index}:${block.kind}`;
        if (block.kind === "heading") {
          return (
            <Text
              accessibilityRole="header"
              key={key}
              selectable
              style={[styles.heading, block.level === 1 && styles.headingOne]}
            >
              {block.text}
            </Text>
          );
        }
        if (block.kind === "code") {
          return (
            <View key={key} style={styles.codeBlock}>
              {block.language !== null && <Text style={styles.codeLanguage}>{block.language}</Text>}
              <Text selectable style={styles.code}>{block.text}</Text>
            </View>
          );
        }
        if (block.kind === "quote") {
          return <View key={key} style={styles.quote}>{inline(block.text)}</View>;
        }
        if (block.kind === "list_item") {
          return (
            <View key={key} style={styles.listItem}>
              <Text style={styles.marker}>{block.marker}</Text>
              <View style={styles.listBody}>{inline(block.text)}</View>
            </View>
          );
        }
        if (block.kind === "separator") return <View key={key} style={styles.separator} />;
        return <View key={key}>{inline(block.text)}</View>;
      })}
      <View style={styles.copyRow}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Copy message"
          hitSlop={8}
          onPress={() => void copy()}
        >
          <Text style={styles.copy}>{copyState === "copied" ? "Copied" : "Copy"}</Text>
        </Pressable>
        {copyState === "failed" && (
          <Text accessibilityLiveRegion="polite" style={styles.copyFailure}>
            Clipboard unavailable
          </Text>
        )}
      </View>
    </View>
  );
}

function createStyles(colors: WorkStationColors) {
  return StyleSheet.create({
    container: { gap: 8 },
    body: { color: colors.text, fontSize: 15, lineHeight: 22 },
    heading: { color: colors.text, fontSize: 17, lineHeight: 23, fontWeight: "800" },
    headingOne: { fontSize: 20, lineHeight: 27 },
    strong: { fontWeight: "800" },
    emphasis: { fontStyle: "italic" },
    inlineCode: {
      color: colors.accent,
      backgroundColor: colors.soft,
      fontFamily: "monospace",
    },
    codeBlock: {
      gap: 6,
      borderColor: colors.line,
      borderWidth: 1,
      borderRadius: 10,
      backgroundColor: colors.background,
      padding: 11,
    },
    codeLanguage: { color: colors.subtle, fontSize: 11, fontWeight: "800" },
    code: { color: colors.text, fontFamily: "monospace", fontSize: 13, lineHeight: 19 },
    quote: { borderLeftColor: colors.accent, borderLeftWidth: 3, paddingLeft: 10 },
    listItem: { flexDirection: "row", alignItems: "flex-start", gap: 8 },
    marker: { minWidth: 18, color: colors.accent, fontWeight: "800", lineHeight: 22 },
    listBody: { flex: 1 },
    separator: { height: 1, backgroundColor: colors.line, marginVertical: 3 },
    copyRow: { minHeight: 36, flexDirection: "row", alignItems: "center", gap: 8 },
    copy: { color: colors.accent, fontSize: 12, fontWeight: "800", paddingVertical: 8 },
    copyFailure: { color: colors.danger, fontSize: 12 },
  });
}
