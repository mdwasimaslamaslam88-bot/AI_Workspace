import {
  parsePrivateDeepLink,
  type PrivateDeepLinkTarget,
} from "@work-station/shared";

type TauriCore = typeof import("@tauri-apps/api/core");

const PRIVATE_TASK_NOTIFICATION = {
  success: {
    title: "WORK STATION task finished",
    body: "Open WORK STATION to review the private result.",
  },
  failure: {
    title: "WORK STATION task needs attention",
    body: "Open WORK STATION to review the private result.",
  },
} as const;

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

export async function setDesktopContentProtected(enabled: boolean): Promise<boolean> {
  if (!isDesktopRuntime()) return false;
  const { getCurrentWindow } = await import("@tauri-apps/api/window");
  await getCurrentWindow().setContentProtected(enabled);
  return true;
}

export async function readDesktopAutostartEnabled(): Promise<boolean> {
  if (!isDesktopRuntime()) return false;
  return (await import("@tauri-apps/plugin-autostart")).isEnabled();
}

export async function writeDesktopAutostartEnabled(enabled: boolean): Promise<void> {
  if (!isDesktopRuntime()) throw new Error("Desktop runtime is unavailable.");
  const autostart = await import("@tauri-apps/plugin-autostart");
  if (enabled) await autostart.enable();
  else await autostart.disable();
}

export async function readDesktopNotificationPermission(): Promise<boolean> {
  if (!isDesktopRuntime()) return false;
  return (await import("@tauri-apps/plugin-notification")).isPermissionGranted();
}

export async function requestDesktopNotificationPermission(): Promise<boolean> {
  if (!isDesktopRuntime()) throw new Error("Desktop runtime is unavailable.");
  const notifications = await import("@tauri-apps/plugin-notification");
  if (await notifications.isPermissionGranted()) return true;
  return (await notifications.requestPermission()) === "granted";
}

export async function notifyDesktopTaskFinished(succeeded: boolean): Promise<boolean> {
  if (!isDesktopRuntime()) return false;
  const notifications = await import("@tauri-apps/plugin-notification");
  if (!(await notifications.isPermissionGranted())) return false;
  notifications.sendNotification(
    succeeded
      ? PRIVATE_TASK_NOTIFICATION.success
      : PRIVATE_TASK_NOTIFICATION.failure,
  );
  return true;
}

export async function listenForDesktopDeepLinks(
  onOpen: (target: PrivateDeepLinkTarget) => void,
): Promise<() => void> {
  if (!isDesktopRuntime()) return () => undefined;
  const deepLinks = await import("@tauri-apps/plugin-deep-link");
  const dispatch = (urls: string[]) => {
    for (const value of urls) {
      const target = parsePrivateDeepLink(value);
      if (target !== null) onOpen(target);
    }
  };
  const unlisten = await deepLinks.onOpenUrl(dispatch);
  try {
    dispatch((await deepLinks.getCurrent()) ?? []);
    return unlisten;
  } catch (error) {
    unlisten();
    throw error;
  }
}
