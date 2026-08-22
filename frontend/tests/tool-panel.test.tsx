import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ToolDescriptor, ToolExecution } from "../src/api/contracts";
import { ToolPanel } from "../src/features/tools/ToolPanel";
import { conversation, rawSecret } from "./fixtures";

const calculator: ToolDescriptor = {
  name: "calculator",
  description: "Evaluate bounded arithmetic.",
  input_schema: { additionalProperties: false, type: "object" },
  permission: "utility",
  timeout_seconds: 1,
  max_output_characters: 1024,
};

const documentSearch: ToolDescriptor = {
  ...calculator,
  name: "document_search",
  description: "Search owned documents.",
  permission: "personal_documents_read",
  timeout_seconds: 5,
  max_output_characters: 12_000,
};

const completed: ToolExecution = {
  id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  conversation_id: conversation.id,
  tool_name: "calculator",
  permission: "utility",
  status: "completed",
  initiator: "explicit_user",
  arguments: { expression: "6*7" },
  result: { value: 42 },
  error_code: null,
  started_at: "2026-08-22T00:00:00Z",
  completed_at: "2026-08-22T00:00:00Z",
  duration_ms: 1,
};

function props() {
  return {
    activeConversationId: conversation.id,
    onClose: vi.fn(),
    onLoad: vi.fn(async (signal?: AbortSignal) => {
      void signal;
      return { tools: [calculator, documentSearch], executions: [] };
    }),
    onExecute: vi.fn(async () => completed),
  };
}


describe("ToolPanel", () => {
  it("shows explicit permission and bounds without a free-form JSON executor", async () => {
    const actions = props();
    render(<ToolPanel {...actions} />);

    expect(await screen.findByRole("option", { name: "Calculator" })).toBeVisible();
    expect(screen.getByText(/Permission: utility/)).toBeVisible();
    expect(screen.getByText(/Deadline: 1s/)).toBeVisible();
    expect(screen.getByText(/no shell, filesystem, code, or network access/)).toBeVisible();
    expect(screen.queryByLabelText(/JSON/)).not.toBeInTheDocument();
    expect(actions.onLoad.mock.calls[0]?.[0]).toBeInstanceOf(AbortSignal);
  });

  it("runs an exact named call in current conversation context and shows result", async () => {
    const actions = props();
    render(<ToolPanel {...actions} />);
    await screen.findByRole("option", { name: "Calculator" });

    await userEvent.type(screen.getByLabelText("Arithmetic expression"), "6*7");
    await userEvent.click(screen.getByRole("button", { name: "Run tool" }));

    await waitFor(() =>
      expect(actions.onExecute).toHaveBeenCalledWith(
        "calculator",
        {
          arguments: { expression: "6*7" },
          conversation_id: conversation.id,
        },
        expect.any(AbortSignal),
      ),
    );
    expect(await screen.findByText(/"value": 42/)).toBeVisible();
    expect(screen.getByText("completed")).toBeVisible();
  });

  it("shows only a fixed error when a tool request throws private details", async () => {
    const actions = props();
    actions.onExecute.mockRejectedValueOnce(new Error(rawSecret));
    render(<ToolPanel {...actions} />);
    await screen.findByRole("option", { name: "Calculator" });

    await userEvent.type(screen.getByLabelText("Arithmetic expression"), "1+1");
    await userEvent.click(screen.getByRole("button", { name: "Run tool" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The tool call could not be completed.",
    );
    expect(document.body.textContent).not.toContain(rawSecret);
  });
});
