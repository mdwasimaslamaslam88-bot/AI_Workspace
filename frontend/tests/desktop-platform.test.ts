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

vi.mock("@tauri-apps/plugin-autostart", () => autostart);
vi.mock("@tauri-apps/plugin-notification", () => notifications);

import {
  notifyDesktopTaskFinished,
  readDesktopAutostartEnabled,
  readDesktopNotificationPermission,
  requestDesktopNotificationPermission,
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
});
