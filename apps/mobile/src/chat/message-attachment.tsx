import type { MessageAttachment } from "@work-station/shared";
import { setAudioModeAsync, useAudioPlayer, useAudioPlayerStatus } from "expo-audio";
import { useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Image, Pressable, StyleSheet, Text, useColorScheme, View } from "react-native";

import { MobileApiError, type MobileApiClient } from "@/api/client";
import { privateAttachmentDetails, privateAttachmentLabel } from "@/chat/attachments";
import { cachePrivateMedia, type CachedPrivateMedia } from "@/media/private-cache";
import { workStationColors } from "@/theme/colors";

function attachmentError(cause: unknown): string {
  return cause instanceof MobileApiError
    ? cause.message
    : "The private attachment could not be loaded.";
}

export function MobileMessageAttachment({
  attachment,
  client,
}: {
  attachment: MessageAttachment;
  client: MobileApiClient;
}) {
  // Kept as a separate wrapper below so React never conditionally calls media hooks.
  return attachment.state === "deleted"
    ? <DeletedAttachment attachment={attachment} />
    : <ActiveAttachment attachment={attachment} client={client} />;
}

function DeletedAttachment({ attachment }: { attachment: MessageAttachment }) {
  const colors = workStationColors(useColorScheme());
  const styles = useMemo(() => createStyles(colors), [colors]);
  return <Text style={styles.tombstone}>{privateAttachmentLabel(attachment)}</Text>;
}

function ActiveAttachment({
  attachment,
  client,
}: {
  attachment: MessageAttachment;
  client: MobileApiClient;
}) {
  const colors = workStationColors(useColorScheme());
  const styles = useMemo(() => createStyles(colors), [colors]);
  const cache = useRef<CachedPrivateMedia | null>(null);
  const request = useRef<AbortController | null>(null);
  const [uri, setUri] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const player = useAudioPlayer(null);
  const playerStatus = useAudioPlayerStatus(player);
  const mediaType = attachment.media_type ?? "";
  const image = mediaType.startsWith("image/");
  const audio = mediaType.startsWith("audio/");

  useEffect(() => () => {
    request.current?.abort();
    cache.current?.remove();
  }, []);

  async function load() {
    if ((!image && !audio) || loading) return;
    const controller = new AbortController();
    request.current?.abort();
    request.current = controller;
    setLoading(true);
    setError(null);
    try {
      const downloaded = await client.downloadAsset(attachment.id, controller.signal);
      const next = cachePrivateMedia(downloaded);
      cache.current?.remove();
      cache.current = next;
      setUri(next.uri);
      if (audio) {
        player.replace(next.uri);
        await setAudioModeAsync({ allowsRecording: false, playsInSilentMode: true });
      }
    } catch (cause) {
      if (!(cause instanceof MobileApiError && cause.kind === "cancelled")) {
        setError(attachmentError(cause));
      }
    } finally {
      if (request.current === controller) {
        request.current = null;
        setLoading(false);
      }
    }
  }

  return (
    <View style={styles.card}>
      <Text numberOfLines={2} style={styles.name}>{privateAttachmentLabel(attachment)}</Text>
      {privateAttachmentDetails(attachment) !== null && (
        <Text style={styles.details}>{privateAttachmentDetails(attachment)}</Text>
      )}
      {(image || audio) && uri === null && (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`Load ${image ? "image" : "audio"} ${privateAttachmentLabel(attachment)}`}
          disabled={loading}
          style={styles.loadButton}
          onPress={() => void load()}
        >
          {loading && <ActivityIndicator color={colors.accent} />}
          <Text style={styles.action}>{loading ? "Loading private media…" : `Load ${image ? "image" : "audio"}`}</Text>
        </Pressable>
      )}
      {image && uri !== null && (
        <Image
          accessibilityLabel={privateAttachmentLabel(attachment)}
          resizeMode="contain"
          source={{ uri }}
          style={styles.image}
        />
      )}
      {audio && uri !== null && (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={playerStatus.playing ? "Pause private audio" : "Play private audio"}
          style={styles.loadButton}
          onPress={() => playerStatus.playing ? player.pause() : player.play()}
        >
          <Text style={styles.action}>{playerStatus.playing ? "Pause audio" : "Play audio"}</Text>
        </Pressable>
      )}
      {error !== null && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}
    </View>
  );
}

function createStyles(colors: ReturnType<typeof workStationColors>) {
  return StyleSheet.create({
    card: {
      gap: 6,
      borderColor: colors.line,
      borderWidth: 1,
      borderRadius: 10,
      backgroundColor: colors.background,
      padding: 10,
    },
    name: { color: colors.text, fontSize: 13, fontWeight: "800" },
    details: { color: colors.subtle, fontSize: 11 },
    tombstone: { color: colors.subtle, fontSize: 12, fontStyle: "italic" },
    loadButton: { minHeight: 44, flexDirection: "row", alignItems: "center", gap: 8 },
    action: { color: colors.accent, fontSize: 12, fontWeight: "800" },
    image: { width: "100%", aspectRatio: 1, borderRadius: 8, backgroundColor: colors.soft },
    error: { color: colors.danger, fontSize: 12 },
  });
}
