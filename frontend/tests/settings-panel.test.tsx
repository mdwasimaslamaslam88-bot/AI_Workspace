import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../src/platform/desktop", () => ({
  isDesktopRuntime: vi.fn(() => false),
  readDesktopAutostartEnabled: vi.fn(async () => false),
  readDesktopNotificationPermission: vi.fn(async () => false),
  requestDesktopNotificationPermission: vi.fn(async () => true),
  setDesktopContentProtected: vi.fn(async () => false),
  writeDesktopAutostartEnabled: vi.fn(async () => undefined),
}));

import { SettingsPanel } from "../src/features/settings/SettingsPanel";
import {
  isDesktopRuntime,
  readDesktopAutostartEnabled,
  readDesktopNotificationPermission,
  requestDesktopNotificationPermission,
  setDesktopContentProtected,
  writeDesktopAutostartEnabled,
} from "../src/platform/desktop";
import {
  productCapabilities,
  rawSecret,
  systemDiagnostics,
} from "./fixtures";

function props() {
  return {
    onClose: vi.fn(),
    onLoad: vi.fn(async (signal?: AbortSignal) => {
      void signal;
      return productCapabilities;
    }),
    onLoadDiagnostics: vi.fn(async (signal?: AbortSignal) => {
      void signal;
      return systemDiagnostics;
    }),
    onLoadSessions: vi.fn(async (signal?: AbortSignal) => {
      void signal;
      return [
        {
          id: "7b914edf-a46b-470c-b3de-9c6109db3fc0",
          label: "This browser",
          created_at: "2026-08-20T09:00:00Z",
          updated_at: "2026-08-21T09:00:00Z",
          is_current: true,
        },
        {
          id: "f7eb9c82-bbb3-4e9b-ad71-46d5b46a815c",
          label: "Phone",
          created_at: "2026-08-19T09:00:00Z",
          updated_at: "2026-08-20T09:00:00Z",
          is_current: false,
        },
      ];
    }),
    appearance: "system" as const,
    onAppearanceChange: vi.fn(),
    onRotateSession: vi.fn(async () => undefined),
    onCreateSession: vi.fn(async (label: string | null) => ({
      access_token: "S".repeat(43),
      token_type: "bearer" as const,
      session: {
        id: "b69885c3-09f5-4f20-b5cb-50112a1dc289",
        label,
        created_at: "2026-08-22T09:00:00Z",
        updated_at: "2026-08-22T09:00:00Z",
        is_current: false,
      },
    })),
    onRenameCurrentSession: vi.fn(async (label: string | null) => ({
      id: "7b914edf-a46b-470c-b3de-9c6109db3fc0",
      label,
      created_at: "2026-08-20T09:00:00Z",
      updated_at: "2026-08-22T09:00:00Z",
      is_current: true,
    })),
    onRevokeSession: vi.fn(async () => undefined),
    onLogout: vi.fn(async () => undefined),
    onManageMemory: vi.fn(),
  };
}

describe("SettingsPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(isDesktopRuntime).mockReturnValue(false);
    vi.mocked(readDesktopAutostartEnabled).mockResolvedValue(false);
    vi.mocked(readDesktopNotificationPermission).mockResolvedValue(false);
    vi.mocked(requestDesktopNotificationPermission).mockResolvedValue(true);
    vi.mocked(setDesktopContentProtected).mockResolvedValue(false);
    vi.mocked(writeDesktopAutostartEnabled).mockResolvedValue(undefined);
  });

  it("shows available features and exact fixed local prerequisites", async () => {
    const actions = props();
    render(<SettingsPanel {...actions} />);

    expect(await screen.findByText("7 of 11 capabilities available now.")).toBeVisible();
    expect(screen.getByText("Document intelligence & RAG")).toBeVisible();
    expect(screen.getByText("Image generation")).toBeVisible();
    expect(screen.getByText(/bounded loopback image adapter/)).toBeVisible();
    expect(screen.getAllByText("unavailable")).toHaveLength(4);
    expect(screen.getByText("Connection mode: LOCAL")).toBeVisible();
    expect(screen.getByText("Remote gateway")).toBeVisible();
    expect(screen.getByText(/Test GPU · 12 GiB · ready/)).toBeVisible();
    expect(actions.onLoad.mock.calls[0]?.[0]).toBeInstanceOf(AbortSignal);
    expect(actions.onLoadDiagnostics.mock.calls[0]?.[0]).toBeInstanceOf(AbortSignal);
    expect(actions.onLoadSessions.mock.calls[0]?.[0]).toBeInstanceOf(AbortSignal);
    expect(document.body.textContent).not.toContain("127.0.0.1");

    await userEvent.click(screen.getByRole("button", { name: "Manage memory" }));
    expect(actions.onManageMemory).toHaveBeenCalledOnce();

    await userEvent.selectOptions(screen.getByLabelText("Theme"), "light");
    expect(actions.onAppearanceChange).toHaveBeenCalledWith("light");

    await userEvent.click(screen.getByRole("button", { name: "Rotate owner token" }));
    expect(
      await screen.findByText("Owner access token rotated and saved on this device."),
    ).toBeVisible();
    expect(actions.onRotateSession).toHaveBeenCalledOnce();
  });

  it("manages separately revocable owner device sessions without listing tokens", async () => {
    const actions = props();
    render(<SettingsPanel {...actions} />);

    expect(await screen.findByText("This browser")).toBeVisible();
    expect(screen.getByText("Phone")).toBeVisible();
    expect(document.body.textContent).not.toContain("S".repeat(43));

    await userEvent.clear(screen.getByLabelText("This device label"));
    await userEvent.type(screen.getByLabelText("This device label"), "Linux workstation");
    await userEvent.click(screen.getByRole("button", { name: "Save label" }));
    expect(actions.onRenameCurrentSession).toHaveBeenCalledWith("Linux workstation");

    await userEvent.type(screen.getByLabelText("New device label"), "Tablet");
    await userEvent.click(screen.getByRole("button", { name: "Issue device token" }));
    expect(actions.onCreateSession).toHaveBeenCalledWith("Tablet");
    expect(await screen.findByLabelText("New device access token")).toHaveValue(
      "S".repeat(43),
    );

    await userEvent.click(screen.getByRole("button", { name: "I saved it" }));
    expect(screen.queryByLabelText("New device access token")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Revoke Phone" }));
    expect(actions.onRevokeSession).toHaveBeenCalledWith(
      "f7eb9c82-bbb3-4e9b-ad71-46d5b46a815c",
    );
  });

  it("clears a one-time device token when the browser leaves the foreground", async () => {
    const actions = props();
    render(<SettingsPanel {...actions} />);
    await screen.findByText("This browser");
    await userEvent.type(screen.getByLabelText("New device label"), "Tablet");
    await userEvent.click(screen.getByRole("button", { name: "Issue device token" }));
    expect(await screen.findByLabelText("New device access token")).toBeVisible();

    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "hidden",
    });
    fireEvent(document, new Event("visibilitychange"));
    expect(screen.queryByLabelText("New device access token")).not.toBeInTheDocument();
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    });
  });

  it("fails closed if packaged desktop content protection is unavailable", async () => {
    vi.mocked(isDesktopRuntime).mockReturnValue(true);
    vi.mocked(setDesktopContentProtected).mockImplementation(async (enabled) => {
      if (enabled) throw new Error("private native detail");
      return true;
    });
    const actions = props();
    render(<SettingsPanel {...actions} />);
    await screen.findByText("This browser");
    await userEvent.type(screen.getByLabelText("New device label"), "Tablet");
    await userEvent.click(screen.getByRole("button", { name: "Issue device token" }));

    await waitFor(() => {
      expect(screen.queryByLabelText("New device access token")).not.toBeInTheDocument();
    });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "one-time token view could not be protected",
    );
    expect(document.body.textContent).not.toContain("private native detail");
  });

  it("shows a fixed error and never renders private failure details", async () => {
    const actions = props();
    actions.onLoad.mockRejectedValueOnce(new Error(rawSecret));
    render(<SettingsPanel {...actions} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Capability diagnostics could not be loaded.",
    );
    expect(document.body.textContent).not.toContain(rawSecret);
  });

  it("aborts capability discovery when the panel closes", () => {
    let capturedSignal: AbortSignal | undefined;
    const actions = props();
    actions.onLoad.mockImplementationOnce((signal?: AbortSignal) => {
      capturedSignal = signal;
      return new Promise(() => undefined);
    });
    const view = render(<SettingsPanel {...actions} />);

    expect(capturedSignal?.aborted).toBe(false);
    view.unmount();
    expect(capturedSignal?.aborted).toBe(true);
  });

  it("redacts session rotation failures and supports device logout", async () => {
    const actions = props();
    actions.onRotateSession.mockRejectedValueOnce(new Error(rawSecret));
    render(<SettingsPanel {...actions} />);

    await userEvent.click(screen.getByRole("button", { name: "Rotate owner token" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The session could not be rotated safely",
    );
    expect(document.body.textContent).not.toContain(rawSecret);

    await userEvent.click(screen.getByRole("button", { name: "Log out on this device" }));
    expect(actions.onLogout).toHaveBeenCalledOnce();
  });

  it("configures packaged desktop startup and private notification permission", async () => {
    vi.mocked(isDesktopRuntime).mockReturnValue(true);
    const actions = props();
    render(<SettingsPanel {...actions} />);

    const autostart = await screen.findByRole("checkbox", {
      name: "Open WORK STATION when I sign in",
    });
    expect(autostart).not.toBeChecked();
    expect(readDesktopAutostartEnabled).toHaveBeenCalledOnce();
    expect(readDesktopNotificationPermission).toHaveBeenCalledOnce();

    await userEvent.click(autostart);
    expect(writeDesktopAutostartEnabled).toHaveBeenCalledWith(true);
    expect(await screen.findByText("Desktop startup enabled.")).toBeVisible();

    await userEvent.click(
      screen.getByRole("button", { name: "Enable notifications" }),
    );
    expect(requestDesktopNotificationPermission).toHaveBeenCalledOnce();
    expect(await screen.findByText("Private desktop notifications enabled.")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Notifications enabled" }),
    ).toBeDisabled();
  });
});
