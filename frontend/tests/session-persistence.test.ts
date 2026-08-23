import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearDesktopSessionToken,
  isDesktopRuntime,
  readDesktopSessionToken,
  writeDesktopSessionToken,
} from "../src/platform/desktop";
import {
  clearPersistedSessionToken,
  readPersistedSessionToken,
  SessionPersistenceError,
  writePersistedSessionToken,
} from "../src/auth/persistence";

vi.mock("../src/platform/desktop", () => ({
  isDesktopRuntime: vi.fn(),
  readDesktopSessionToken: vi.fn(),
  writeDesktopSessionToken: vi.fn(),
  clearDesktopSessionToken: vi.fn(),
}));

describe("cross-platform session persistence", () => {
  beforeEach(() => {
    vi.mocked(isDesktopRuntime).mockReturnValue(true);
    vi.mocked(readDesktopSessionToken).mockReset();
    vi.mocked(writeDesktopSessionToken).mockReset();
    vi.mocked(clearDesktopSessionToken).mockReset();
  });

  it("uses only the native credential bridge in the desktop runtime", async () => {
    vi.mocked(readDesktopSessionToken).mockResolvedValue("native-token");

    expect(await readPersistedSessionToken()).toBe("native-token");
    await writePersistedSessionToken("native-token");
    await clearPersistedSessionToken();

    expect(writeDesktopSessionToken).toHaveBeenCalledWith("native-token");
    expect(clearDesktopSessionToken).toHaveBeenCalledOnce();
    expect(window.sessionStorage.length).toBe(0);
  });

  it("redacts native credential-store failures", async () => {
    vi.mocked(readDesktopSessionToken).mockRejectedValue(
      new Error("provider-specific secret detail"),
    );

    await expect(readPersistedSessionToken()).rejects.toEqual(
      new SessionPersistenceError(),
    );
  });
});
