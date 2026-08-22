import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { MemorySetting, PersonalMemory } from "../src/api/contracts";
import { MemoryPanel } from "../src/features/memory/MemoryPanel";


const active: PersonalMemory = {
  id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  category: "preference",
  state: "active",
  content: "Use concise answers.",
  provenance_kind: "explicit_user_entry",
  created_at: "2026-08-22T00:00:00Z",
  updated_at: "2026-08-22T00:00:00Z",
  deleted_at: null,
};

const deleted: PersonalMemory = {
  ...active,
  id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  state: "deleted",
  content: null,
  deleted_at: "2026-08-22T01:00:00Z",
  updated_at: "2026-08-22T01:00:00Z",
};

const enabled: MemorySetting = {
  enabled: true,
  created_at: null,
  updated_at: null,
};

function props() {
  return {
    onClose: vi.fn(),
    onLoad: vi.fn(async (signal?: AbortSignal) => {
      void signal;
      return { memories: [active, deleted], setting: enabled };
    }),
    onCreate: vi.fn(async (request) => ({
      ...active,
      id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      category: request.category,
      content: request.content,
    })),
    onForget: vi.fn(async () => ({
      ...active,
      state: "deleted" as const,
      content: null,
      deleted_at: "2026-08-22T02:00:00Z",
    })),
    onSetEnabled: vi.fn(async (value: boolean) => ({
      ...enabled,
      enabled: value,
    })),
  };
}

describe("MemoryPanel", () => {
  it("loads inspectable explicit memories and renders deleted content as a tombstone", async () => {
    const actions = props();
    const { container } = render(<MemoryPanel {...actions} />);

    expect(await screen.findByText("Use concise answers.")).toBeVisible();
    expect(screen.getByText("Forgotten memory")).toBeVisible();
    expect(screen.getByText(/Only entries you explicitly save/)).toBeVisible();
    expect(container.textContent).not.toContain("embedding");
    expect(actions.onLoad).toHaveBeenCalledOnce();
    expect(actions.onLoad.mock.calls[0]?.[0]).toBeInstanceOf(AbortSignal);
  });

  it("creates, disables, and forgets memory only through explicit controls", async () => {
    const actions = props();
    render(<MemoryPanel {...actions} />);
    await screen.findByText("Use concise answers.");

    await userEvent.selectOptions(screen.getByLabelText("Category"), "instruction");
    await userEvent.type(screen.getByLabelText("Memory"), "Always show steps.");
    await userEvent.click(screen.getByRole("button", { name: "Save explicitly" }));
    await waitFor(() =>
      expect(actions.onCreate).toHaveBeenCalledWith(
        { category: "instruction", content: "Always show steps." },
        expect.any(AbortSignal),
      ),
    );
    expect(await screen.findByText("Always show steps.")).toBeVisible();

    await userEvent.click(screen.getByRole("checkbox", { name: /Use saved memory/ }));
    await waitFor(() =>
      expect(actions.onSetEnabled).toHaveBeenCalledWith(
        false,
        expect.any(AbortSignal),
      ),
    );

    await userEvent.click(screen.getAllByRole("button", { name: "Forget" })[1]);
    await waitFor(() =>
      expect(actions.onForget).toHaveBeenCalledWith(
        active.id,
        expect.any(AbortSignal),
      ),
    );
    expect(screen.getAllByText("Forgotten memory")).toHaveLength(2);
  });

  it("renders safe fixed errors without reflecting thrown details", async () => {
    const actions = props();
    actions.onLoad.mockRejectedValueOnce(new Error("PRIVATE_MEMORY_SENTINEL"));
    render(<MemoryPanel {...actions} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Personal memory could not be loaded.",
    );
    expect(document.body.textContent).not.toContain("PRIVATE_MEMORY_SENTINEL");
  });
});
