import { beforeEach, describe, expect, it, vi } from "vitest";

const autostart = vi.hoisted(() => ({
  disable: vi.fn(async () => undefined),
  enable: vi.fn(async () => undefined),
  isEnabled: vi.fn(async () => false),
}));
const notifications = vi.hoisted(() => ({
  isPermissionGranted: vi.fn(async () => false),
  requestPermission: vi.fn(async () => "granted" as NotificationPermission),
  sendNotification: vi.fn(),
}));
const deepLinks = vi.hoisted(() => ({
  getCurrent: vi.fn<() => Promise<string[] | null>>(async () => null),
  onOpenUrl: vi.fn<
    (handler: (urls: string[]) => void) => Promise<() => void>
  >(async () => () => undefined),
}));
const currentWindow = vi.hoisted(() => ({
  setContentProtected: vi.fn(async () => undefined),
}));

vi.mock("@tauri-apps/plugin-autostart", () => autostart);
vi.mock("@tauri-apps/plugin-notification", () => notifications);
vi.mock("@tauri-apps/plugin-deep-link", () => deepLinks);
vi.mock("@tauri-apps/api/window", () => ({
  getCurrentWindow: () => currentWindow,
}));

import {
  notifyDesktopTaskFinished,
  listenForDesktopDeepLinks,
  readDesktopAutostartEnabled,
  readDesktopNotificationPermission,
  requestDesktopNotificationPermission,
  setDesktopContentProtected,
  writeDesktopAutostartEnabled,
} from "../src/platform/desktop";

describe("desktop native preferences", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(window, "__TAURI_INTERNALS__", {
      configurable: true,
      value: {},
    });
  });

  it("reads and writes only the bounded native autostart preference", async () => {
    autostart.isEnabled.mockResolvedValueOnce(true);
    await expect(readDesktopAutostartEnabled()).resolves.toBe(true);
    await writeDesktopAutostartEnabled(true);
    await writeDesktopAutostartEnabled(false);

    expect(autostart.enable).toHaveBeenCalledOnce();
    expect(autostart.disable).toHaveBeenCalledOnce();
  });

  it("protects transient credential content only in the packaged window", async () => {
    await expect(setDesktopContentProtected(true)).resolves.toBe(true);
    await expect(setDesktopContentProtected(false)).resolves.toBe(true);
    expect(currentWindow.setContentProtected).toHaveBeenNthCalledWith(1, true);
    expect(currentWindow.setContentProtected).toHaveBeenNthCalledWith(2, false);
  });

  it("requests notification permission only when needed", async () => {
    notifications.isPermissionGranted.mockResolvedValueOnce(false);
    await expect(readDesktopNotificationPermission()).resolves.toBe(false);
    notifications.isPermissionGranted.mockResolvedValueOnce(false);
    notifications.requestPermission.mockResolvedValueOnce("denied");
    await expect(requestDesktopNotificationPermission()).resolves.toBe(false);
    notifications.isPermissionGranted.mockResolvedValueOnce(true);
    await expect(requestDesktopNotificationPermission()).resolves.toBe(true);
    expect(notifications.requestPermission).toHaveBeenCalledOnce();
  });

  it("sends fixed private success and failure notices only after permission", async () => {
    notifications.isPermissionGranted.mockResolvedValueOnce(false);
    await expect(notifyDesktopTaskFinished(true)).resolves.toBe(false);
    expect(notifications.sendNotification).not.toHaveBeenCalled();

    notifications.isPermissionGranted.mockResolvedValueOnce(true);
    await expect(notifyDesktopTaskFinished(true)).resolves.toBe(true);
    notifications.isPermissionGranted.mockResolvedValueOnce(true);
    await expect(notifyDesktopTaskFinished(false)).resolves.toBe(true);

    expect(notifications.sendNotification).toHaveBeenNthCalledWith(1, {
      title: "WORK STATION task finished",
      body: "Open WORK STATION to review the private result.",
    });
    expect(notifications.sendNotification).toHaveBeenNthCalledWith(2, {
      title: "WORK STATION task needs attention",
      body: "Open WORK STATION to review the private result.",
    });
  });

  it("keeps native integrations unreachable in an ordinary browser", async () => {
    Reflect.deleteProperty(window, "__TAURI_INTERNALS__");

    await expect(readDesktopAutostartEnabled()).resolves.toBe(false);
    await expect(readDesktopNotificationPermission()).resolves.toBe(false);
    await expect(setDesktopContentProtected(true)).resolves.toBe(false);
    await expect(notifyDesktopTaskFinished(true)).resolves.toBe(false);
    await expect(writeDesktopAutostartEnabled(true)).rejects.toThrow(
      "Desktop runtime is unavailable.",
    );
    await expect(requestDesktopNotificationPermission()).rejects.toThrow(
      "Desktop runtime is unavailable.",
    );
    expect(autostart.isEnabled).not.toHaveBeenCalled();
    expect(notifications.isPermissionGranted).not.toHaveBeenCalled();
    expect(notifications.sendNotification).not.toHaveBeenCalled();
  });

  it("dispatches only allowlisted initial and running-instance links", async () => {
    const unlisten = vi.fn<() => void>(() => undefined);
    let runningHandler: ((urls: string[]) => void) | undefined;
    deepLinks.onOpenUrl.mockImplementationOnce(async (handler: (urls: string[]) => void) => {
      runningHandler = handler;
      return unlisten;
    });
    deepLinks.getCurrent.mockResolvedValueOnce([
      "work-station://settings",
      "work-station://chat?unexpected=value",
    ]);
    const opened = vi.fn();

    const dispose = await listenForDesktopDeepLinks(opened);
    expect(opened).toHaveBeenCalledOnce();
    expect(opened).toHaveBeenCalledWith("settings");

    runningHandler?.([
      "work-station://workflows",
      "https://example.invalid/private",
    ]);
    expect(opened).toHaveBeenNthCalledWith(2, "workflows");
    expect(opened).toHaveBeenCalledTimes(2);

    dispose();
    expect(unlisten).toHaveBeenCalledOnce();
  });
});
