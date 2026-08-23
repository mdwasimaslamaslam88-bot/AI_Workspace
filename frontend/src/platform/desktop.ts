type TauriCore = typeof import("@tauri-apps/api/core");

export function isDesktopRuntime(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

async function core(): Promise<TauriCore> {
  if (!isDesktopRuntime()) throw new Error("Desktop runtime is unavailable.");
  return import("@tauri-apps/api/core");
}

export async function readDesktopSessionToken(): Promise<string | null> {
  return (await core()).invoke<string | null>("read_session_token");
}

export async function writeDesktopSessionToken(token: string): Promise<void> {
  await (await core()).invoke("write_session_token", { token });
}

export async function clearDesktopSessionToken(): Promise<void> {
  await (await core()).invoke("clear_session_token");
}
