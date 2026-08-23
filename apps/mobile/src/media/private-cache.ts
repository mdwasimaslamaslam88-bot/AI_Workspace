import { File, Paths } from "expo-file-system";

import { createIdempotencyKey, type PrivateAssetContent } from "@/api/client";

const privateMediaExtensions: Readonly<Record<string, string>> = {
  "audio/mpeg": "mp3",
  "audio/mp4": "m4a",
  "audio/ogg": "ogg",
  "audio/wav": "wav",
  "image/jpeg": "jpg",
  "image/png": "png",
  "image/webp": "webp",
};

export interface CachedPrivateMedia {
  uri: string;
  remove: () => void;
}

export function cachePrivateMedia(content: PrivateAssetContent): CachedPrivateMedia {
  const extension = privateMediaExtensions[content.mediaType];
  if (extension === undefined || content.bytes.byteLength === 0) {
    throw new Error("Unsupported private media.");
  }
  const file = new File(
    Paths.cache,
    `work-station-private-${createIdempotencyKey()}.${extension}`,
  );
  file.create();
  try {
    file.write(content.bytes);
  } catch (cause) {
    if (file.exists) file.delete();
    throw cause;
  }
  return {
    uri: file.uri,
    remove: () => {
      if (file.exists) file.delete();
    },
  };
}
