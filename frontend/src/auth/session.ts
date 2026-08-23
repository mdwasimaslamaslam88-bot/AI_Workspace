const TOKEN_STORAGE_KEY = "work-station.bearer-token";
const LEGACY_TOKEN_STORAGE_KEY = "ai-workspace.bearer-token";
const MODEL_STORAGE_KEY = "work-station.model-id";
const LEGACY_MODEL_STORAGE_KEY = "ai-workspace.model-id";

function browserSessionStorage(): Storage | null {
  return typeof window === "undefined" ? null : window.sessionStorage;
}

export function readSessionToken(
  storage = browserSessionStorage(),
): string | null {
  const token =
    storage?.getItem(TOKEN_STORAGE_KEY) ??
    storage?.getItem(LEGACY_TOKEN_STORAGE_KEY) ??
    null;
  if (token && storage?.getItem(TOKEN_STORAGE_KEY) === null) {
    storage.setItem(TOKEN_STORAGE_KEY, token);
    storage.removeItem(LEGACY_TOKEN_STORAGE_KEY);
  }
  return token && token.length > 0 ? token : null;
}

export function writeSessionToken(
  token: string,
  storage = browserSessionStorage(),
): void {
  if (token.length === 0) throw new Error("A bearer token is required.");
  storage?.setItem(TOKEN_STORAGE_KEY, token);
  storage?.removeItem(LEGACY_TOKEN_STORAGE_KEY);
}

export function clearSessionToken(
  storage = browserSessionStorage(),
): void {
  storage?.removeItem(TOKEN_STORAGE_KEY);
  storage?.removeItem(LEGACY_TOKEN_STORAGE_KEY);
}

export function readModelPreference(
  storage = browserSessionStorage(),
): string | null {
  const modelId =
    storage?.getItem(MODEL_STORAGE_KEY) ??
    storage?.getItem(LEGACY_MODEL_STORAGE_KEY) ??
    null;
  if (modelId && storage?.getItem(MODEL_STORAGE_KEY) === null) {
    storage.setItem(MODEL_STORAGE_KEY, modelId);
    storage.removeItem(LEGACY_MODEL_STORAGE_KEY);
  }
  return modelId;
}

export function writeModelPreference(
  modelId: string,
  storage = browserSessionStorage(),
): void {
  storage?.setItem(MODEL_STORAGE_KEY, modelId);
  storage?.removeItem(LEGACY_MODEL_STORAGE_KEY);
}

export function clearModelPreference(
  storage = browserSessionStorage(),
): void {
  storage?.removeItem(MODEL_STORAGE_KEY);
  storage?.removeItem(LEGACY_MODEL_STORAGE_KEY);
}
