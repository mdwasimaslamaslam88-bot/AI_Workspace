import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SettingsPanel } from "../src/features/settings/SettingsPanel";
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
    appearance: "system" as const,
    onAppearanceChange: vi.fn(),
    onRotateSession: vi.fn(async () => undefined),
    onLogout: vi.fn(),
    onManageMemory: vi.fn(),
  };
}

describe("SettingsPanel", () => {
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
});
