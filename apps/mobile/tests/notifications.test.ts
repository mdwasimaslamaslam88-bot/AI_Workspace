import { beforeEach, describe, expect, it, vi } from "vitest";

import * as Notifications from "expo-notifications";

import {
  loadPrivateNotifications,
  notifyTaskFinished,
  privateTaskNotificationContent,
  requestPrivateNotificationPermission,
} from "../src/notifications/private-notifications";

vi.mock("expo-notifications", () => ({
  getPermissionsAsync: vi.fn(),
  requestPermissionsAsync: vi.fn(),
  scheduleNotificationAsync: vi.fn(),
}));

vi.mock("expo-constants", () => ({
  default: { expoVersion: null },
}));

describe("private mobile notifications", () => {
  beforeEach(() => vi.clearAllMocks());

  it("requests permission only when it has not already been granted", async () => {
    vi.mocked(Notifications.getPermissionsAsync).mockResolvedValueOnce({ granted: true } as never);
    await expect(requestPrivateNotificationPermission()).resolves.toBe(true);
    expect(Notifications.requestPermissionsAsync).not.toHaveBeenCalled();

    vi.mocked(Notifications.getPermissionsAsync).mockResolvedValueOnce({ granted: false } as never);
    vi.mocked(Notifications.requestPermissionsAsync).mockResolvedValueOnce({ granted: false } as never);
    await expect(requestPrivateNotificationPermission()).resolves.toBe(false);
  });

  it("uses a generic preview with no private task content", async () => {
    const content = privateTaskNotificationContent(false);
    expect(JSON.stringify(content)).toBe(
      JSON.stringify({
        title: "WORK STATION task needs attention",
        body: "Open WORK STATION to review the private result.",
        data: { route: "/" },
      }),
    );

    await notifyTaskFinished(true);
    expect(Notifications.scheduleNotificationAsync).toHaveBeenCalledWith({
      content: privateTaskNotificationContent(true),
      trigger: null,
    });
  });

  it("keeps Expo Go usable when native notifications are unavailable", async () => {
    const unavailable = async () => null;

    await expect(requestPrivateNotificationPermission(unavailable)).resolves.toBe(false);
    await expect(notifyTaskFinished(true, unavailable)).resolves.toBeUndefined();
    expect(Notifications.getPermissionsAsync).not.toHaveBeenCalled();
    expect(Notifications.requestPermissionsAsync).not.toHaveBeenCalled();
    expect(Notifications.scheduleNotificationAsync).not.toHaveBeenCalled();
  });

  it("does not import the unsupported notification module inside Expo Go", async () => {
    const importNotifications = vi.fn(async () => Notifications);

    await expect(loadPrivateNotifications(true, importNotifications)).resolves.toBeNull();
    expect(importNotifications).not.toHaveBeenCalled();
  });

  it("fails closed when the native permission APIs reject", async () => {
    vi.mocked(Notifications.getPermissionsAsync).mockRejectedValueOnce(
      new Error("native notification failure"),
    );

    await expect(requestPrivateNotificationPermission()).resolves.toBe(false);
    expect(Notifications.requestPermissionsAsync).not.toHaveBeenCalled();
  });
});
