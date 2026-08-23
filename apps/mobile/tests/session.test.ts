import { beforeEach, describe, expect, it, vi } from "vitest";

import * as SecureStore from "expo-secure-store";
import { clearSecureSession, readSecureSession, writeSecureSession } from "../src/auth/session";

vi.mock("expo-secure-store", () => ({
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: 6,
  getItemAsync: vi.fn(),
  setItemAsync: vi.fn(),
  deleteItemAsync: vi.fn(),
}));

describe("mobile secure session contract", () => {
  beforeEach(() => {
    vi.mocked(SecureStore.getItemAsync).mockReset();
    vi.mocked(SecureStore.setItemAsync).mockReset();
    vi.mocked(SecureStore.deleteItemAsync).mockReset();
  });

  it("stores the owner bearer only in platform secure storage", async () => {
    vi.mocked(SecureStore.getItemAsync).mockResolvedValue("device-token");

    await writeSecureSession("device-token");
    expect(await readSecureSession()).toBe("device-token");
    await clearSecureSession();

    expect(SecureStore.setItemAsync).toHaveBeenCalledWith(
      "work-station.owner-session",
      "device-token",
      { keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY },
    );
    expect(SecureStore.deleteItemAsync).toHaveBeenCalledOnce();
  });

  it("rejects empty and oversized bearer values", async () => {
    await expect(writeSecureSession("")).rejects.toThrow("valid session");
    await expect(writeSecureSession("x".repeat(513))).rejects.toThrow("valid session");
    expect(SecureStore.setItemAsync).not.toHaveBeenCalled();
  });
});
