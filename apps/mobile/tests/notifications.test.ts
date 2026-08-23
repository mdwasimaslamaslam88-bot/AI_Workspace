import { beforeEach, describe, expect, it, vi } from "vitest";

import * as Notifications from "expo-notifications";

import {
  notifyTaskFinished,
  privateTaskNotificationContent,
  requestPrivateNotificationPermission,
} from "../src/notifications/private-notifications";

vi.mock("expo-notifications", () => ({
  getPermissionsAsync: vi.fn(),
  requestPermissionsAsync: vi.fn(),
  scheduleNotificationAsync: vi.fn(),
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
});
