import type { MessageAttachment } from "@work-station/shared";
import { setAudioModeAsync, useAudioPlayer, useAudioPlayerStatus } from "expo-audio";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ActivityIndicator, Image, Pressable, StyleSheet, Text, useColorScheme, View } from "react-native";

import { MobileApiError, type MobileApiClient } from "@/api/client";
import {
  privateAttachmentDetails,
  privateAttachmentKind,
  privateAttachmentLabel,
} from "@/chat/attachments";
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
  if (attachment.state === "deleted") return <DeletedAttachment attachment={attachment} />;
  const kind = privateAttachmentKind(attachment);
  if (kind === "audio") return <AudioAttachment attachment={attachment} client={client} />;
  if (kind === "image") return <ImageAttachment attachment={attachment} client={client} />;
  return <AttachmentFrame attachment={attachment} />;
}

function DeletedAttachment({ attachment }: { attachment: MessageAttachment }) {
  const colors = workStationColors(useColorScheme());
  const styles = useMemo(() => createStyles(colors), [colors]);
  return <Text style={styles.tombstone}>{privateAttachmentLabel(attachment)}</Text>;
}

interface PrivateMediaState {
  error: string | null;
  load: () => Promise<void>;
  loading: boolean;
  uri: string | null;
}

function usePrivateMedia(
  attachment: MessageAttachment,
  client: MobileApiClient,
  prepare?: (uri: string) => Promise<void>,
): PrivateMediaState {
  const cache = useRef<CachedPrivateMedia | null>(null);
  const mounted = useRef(true);
  const request = useRef<AbortController | null>(null);
  const [uri, setUri] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      request.current?.abort();
      cache.current?.remove();
    };
  }, []);

  async function load() {
    if (loading) return;
    const controller = new AbortController();
    request.current?.abort();
    request.current = controller;
    setLoading(true);
    setError(null);
    try {
      const downloaded = await client.downloadAsset(attachment.id, controller.signal);
      const next = cachePrivateMedia(downloaded);
      if (!mounted.current || controller.signal.aborted) {
        next.remove();
        return;
      }
      try {
        await prepare?.(next.uri);
      } catch (cause) {
        next.remove();
        throw cause;
      }
      if (!mounted.current || controller.signal.aborted) {
        next.remove();
        return;
      }
      cache.current?.remove();
      cache.current = next;
      setUri(next.uri);
    } catch (cause) {
      if (mounted.current && !(cause instanceof MobileApiError && cause.kind === "cancelled")) {
        setError(attachmentError(cause));
      }
    } finally {
      if (request.current === controller) {
        request.current = null;
        if (mounted.current) setLoading(false);
      }
    }
  }

  return { error, load, loading, uri };
}

function ImageAttachment({
  attachment,
  client,
}: {
  attachment: MessageAttachment;
  client: MobileApiClient;
}) {
  const colors = workStationColors(useColorScheme());
  const styles = useMemo(() => createStyles(colors), [colors]);
  const media = usePrivateMedia(attachment, client);

  return (
    <AttachmentFrame attachment={attachment} error={media.error}>
      {media.uri === null ? (
        <LoadPrivateMediaButton
          kind="image"
          label={privateAttachmentLabel(attachment)}
          loading={media.loading}
          onPress={media.load}
        />
      ) : (
        <Image
          accessibilityLabel={privateAttachmentLabel(attachment)}
          resizeMode="contain"
          source={{ uri: media.uri }}
          style={styles.image}
        />
      )}
    </AttachmentFrame>
  );
}

function AudioAttachment({
  attachment,
  client,
}: {
  attachment: MessageAttachment;
  client: MobileApiClient;
}) {
  const colors = workStationColors(useColorScheme());
  const styles = useMemo(() => createStyles(colors), [colors]);
  const player = useAudioPlayer(null);
  const playerStatus = useAudioPlayerStatus(player);
  const prepare = useCallback(async (uri: string) => {
    player.replace(uri);
    await setAudioModeAsync({ allowsRecording: false, playsInSilentMode: true });
  }, [player]);
  const media = usePrivateMedia(attachment, client, prepare);

  return (
    <AttachmentFrame attachment={attachment} error={media.error}>
      {media.uri === null ? (
        <LoadPrivateMediaButton
          kind="audio"
          label={privateAttachmentLabel(attachment)}
          loading={media.loading}
          onPress={media.load}
        />
      ) : (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={playerStatus.playing ? "Pause private audio" : "Play private audio"}
          style={styles.loadButton}
          onPress={() => playerStatus.playing ? player.pause() : player.play()}
        >
          <Text style={styles.action}>{playerStatus.playing ? "Pause audio" : "Play audio"}</Text>
        </Pressable>
      )}
    </AttachmentFrame>
  );
}

function LoadPrivateMediaButton({
  kind,
  label,
  loading,
  onPress,
}: {
  kind: "audio" | "image";
  label: string;
  loading: boolean;
  onPress: () => Promise<void>;
}) {
  const colors = workStationColors(useColorScheme());
  const styles = useMemo(() => createStyles(colors), [colors]);
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`Load ${kind} ${label}`}
      disabled={loading}
      style={styles.loadButton}
      onPress={() => void onPress()}
    >
      {loading && <ActivityIndicator color={colors.accent} />}
      <Text style={styles.action}>{loading ? "Loading private media…" : `Load ${kind}`}</Text>
    </Pressable>
  );
}

function AttachmentFrame({
  attachment,
  children,
  error = null,
}: {
  attachment: MessageAttachment;
  children?: ReactNode;
  error?: string | null;
}) {
  const colors = workStationColors(useColorScheme());
  const styles = useMemo(() => createStyles(colors), [colors]);

  return (
    <View style={styles.card}>
      <Text numberOfLines={2} style={styles.name}>{privateAttachmentLabel(attachment)}</Text>
      {privateAttachmentDetails(attachment) !== null && (
        <Text style={styles.details}>{privateAttachmentDetails(attachment)}</Text>
      )}
      {children}
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
