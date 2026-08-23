import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../src/app/App";
import { readSessionToken, writeSessionToken } from "../src/auth/session";
import {
  conversation,
  errorEnvelope,
  jsonResponse,
  message,
  model,
  productCapabilities,
  rawSecret,
  systemDiagnostics,
  token,
  user,
  visionModel,
} from "./fixtures";

function installWorkspaceFetch(options: {
  generationStatus?: 201 | 409 | "pending";
  models?: Array<typeof model>;
} = {}) {
  let generated = false;
  let messageReads = 0;
  const calls: Array<{ url: URL; init: RequestInit | undefined }> = [];
  const fetchMock = vi.fn(async (input: URL | RequestInfo, init?: RequestInit) => {
    const url = new URL(input.toString());
    calls.push({ url, init });

    if (url.pathname === "/api/v1/users/me") return jsonResponse(user);
    if (
      url.pathname === "/api/v1/users/me/sessions/current" &&
      init?.method === "DELETE"
    ) {
      return new Response(null, { status: 204 });
    }
    if (url.pathname === "/api/v1/users/me/sessions") {
      return jsonResponse({
        items: [
          {
            id: "7b914edf-a46b-470c-b3de-9c6109db3fc0",
            label: "Test browser",
            created_at: user.created_at,
            updated_at: user.updated_at,
            is_current: true,
          },
        ],
      });
    }
    if (url.pathname === "/api/v1/memories/settings") {
      return jsonResponse({ enabled: true, created_at: null, updated_at: null });
    }
    if (url.pathname === "/api/v1/memories") {
      return jsonResponse({ items: [] });
    }
    if (url.pathname === "/api/v1/tools") {
      return jsonResponse({
        items: [
          {
            name: "calculator",
            description: "Bounded arithmetic.",
            input_schema: { additionalProperties: false, type: "object" },
            permission: "utility",
            timeout_seconds: 1,
            max_output_characters: 1024,
          },
        ],
      });
    }
    if (url.pathname === "/api/v1/tools/executions") {
      return jsonResponse({ items: [] });
    }
    if (url.pathname === "/api/v1/ai/capabilities") {
      return jsonResponse({ items: productCapabilities });
    }
    if (url.pathname === "/api/v1/diagnostics") {
      return jsonResponse(systemDiagnostics);
    }
    if (url.pathname === "/api/v1/workflows") {
      return jsonResponse({ items: [] });
    }
    if (url.pathname === "/api/v1/ai/models") {
      return jsonResponse({ items: options.models ?? [model] });
    }
    if (url.pathname === "/api/v1/conversations" && (init?.method ?? "GET") === "GET") {
      return jsonResponse({ items: [conversation], next_cursor: null });
    }
    if (url.pathname === `/api/v1/conversations/${conversation.id}`) {
      return jsonResponse(conversation);
    }
    if (
      url.pathname ===
        `/api/v1/conversations/${conversation.id}/messages` &&
      (init?.method ?? "GET") === "GET"
    ) {
      messageReads += 1;
      return jsonResponse({
        items: generated
          ? [message(1, "user", "hello"), message(2, "user", "next"), message(3, "assistant", "answer")]
          : [message(1, "user", "hello")],
        next_cursor: null,
      });
    }
    if (
      url.pathname ===
        `/api/v1/conversations/${conversation.id}/messages/generate` &&
      init?.method === "POST"
    ) {
      if (options.generationStatus === "pending") {
        return await new Promise<Response>((_resolve, reject) => {
          init.signal?.addEventListener("abort", () => {
            reject(new DOMException("aborted", "AbortError"));
          });
        });
      }
      if (options.generationStatus === 409) {
        return jsonResponse(errorEnvelope(), 409, "request-conflict");
      }
      generated = true;
      return jsonResponse(
        {
          model_id: model.model_id,
          message: message(3, "assistant", "answer"),
        },
        201,
      );
    }
    throw new Error(`Unexpected test request: ${url.pathname}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock, messageReads: () => messageReads };
}

describe("App integration", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("restores a valid session, renders safe identity, and clears it on logout", async () => {
    writeSessionToken(token);
    installWorkspaceFetch();
    render(<App />);

    expect(await screen.findByText(user.id)).toBeVisible();
    expect(await screen.findByRole("button", { name: /Local chat/ })).toBeVisible();
    expect(document.body.textContent).not.toContain(token);
    expect(document.body.textContent).not.toContain("127.0.0.1:11434");

    await userEvent.click(screen.getByRole("button", { name: "Logout" }));
    expect(readSessionToken()).toBeNull();
    expect(screen.getByRole("heading", { name: /Connect to your Personal AI/ })).toBeVisible();
  });


  it("loads personal memory only when the explicit control is opened", async () => {
    writeSessionToken(token);
    const workspace = installWorkspaceFetch();
    render(<App />);

    await screen.findByRole("button", { name: /Local chat/ });
    expect(
      workspace.calls.filter((call) => call.url.pathname.startsWith("/api/v1/memories")),
    ).toHaveLength(0);

    await userEvent.click(screen.getByRole("button", { name: "Memory" }));

    expect(
      await screen.findByRole("heading", { name: "What your AI remembers" }),
    ).toBeVisible();
    expect(screen.getByText("No personal memories saved.")).toBeVisible();
    await waitFor(() =>
      expect(
        workspace.calls.filter((call) =>
          call.url.pathname.startsWith("/api/v1/memories"),
        ),
      ).toHaveLength(2),
    );
  });

  it("loads bounded tools only when the explicit control is opened", async () => {
    writeSessionToken(token);
    const workspace = installWorkspaceFetch();
    render(<App />);

    await screen.findByRole("button", { name: /Local chat/ });
    expect(
      workspace.calls.filter((call) => call.url.pathname.startsWith("/api/v1/tools")),
    ).toHaveLength(0);

    await userEvent.click(screen.getByRole("button", { name: "Tools" }));

    expect(await screen.findByRole("heading", { name: "Local tools" })).toBeVisible();
    expect(screen.getByText("No tool calls yet.")).toBeVisible();
    await waitFor(() =>
      expect(
        workspace.calls.filter((call) =>
          call.url.pathname.startsWith("/api/v1/tools"),
        ),
      ).toHaveLength(2),
    );
  });

  it("loads bounded workflows only when the explicit control is opened", async () => {
    writeSessionToken(token);
    const workspace = installWorkspaceFetch();
    render(<App />);

    await screen.findByRole("button", { name: /Local chat/ });
    expect(
      workspace.calls.filter((call) =>
        call.url.pathname.startsWith("/api/v1/workflows"),
      ),
    ).toHaveLength(0);

    await userEvent.click(screen.getByRole("button", { name: "Workflows" }));

    expect(
      await screen.findByRole("heading", { name: "Research tasks" }),
    ).toBeVisible();
    expect(screen.getByText("No workflows yet.")).toBeVisible();
    await waitFor(() =>
      expect(
        workspace.calls.filter((call) =>
          call.url.pathname.startsWith("/api/v1/workflows"),
        ),
      ).toHaveLength(1),
    );
  });

  it("loads diagnostics only when Settings opens and links to memory controls", async () => {
    writeSessionToken(token);
    const workspace = installWorkspaceFetch();
    render(<App />);

    await screen.findByRole("button", { name: /Local chat/ });
    expect(
      workspace.calls.filter((call) =>
        ["/api/v1/ai/capabilities", "/api/v1/diagnostics"].includes(
          call.url.pathname,
        ),
      ),
    ).toHaveLength(0);

    await userEvent.click(screen.getByRole("button", { name: "Settings" }));

    expect(
      await screen.findByRole("heading", { name: "Settings & diagnostics" }),
    ).toBeVisible();
    expect(screen.getByText("7 of 11 capabilities available now.")).toBeVisible();
    expect(screen.getByText(/bounded loopback image adapter/)).toBeVisible();
    expect(screen.getByText("Connection mode: LOCAL")).toBeVisible();
    await waitFor(() =>
      expect(
        workspace.calls.filter((call) =>
          call.url.pathname === "/api/v1/ai/capabilities",
        ),
      ).toHaveLength(1),
    );
    expect(
      workspace.calls.filter((call) => call.url.pathname === "/api/v1/diagnostics"),
    ).toHaveLength(1);

    await userEvent.click(screen.getByRole("button", { name: "Manage memory" }));
    expect(
      await screen.findByRole("heading", { name: "What your AI remembers" }),
    ).toBeVisible();
  });

  it("propagates public vision capability from discovered model metadata", async () => {
    writeSessionToken(token);
    installWorkspaceFetch({ models: [visionModel, model] });
    render(<App />);

    expect(
      await screen.findByRole("option", {
        name: /Local Vision Model · 8B · Vision/,
      }),
    ).toBeVisible();
    expect(screen.getByText("vision input")).toBeVisible();
    expect(document.body.textContent).not.toContain(token);
  });

  it("clears an invalid stored token and returns to the connect screen", async () => {
    writeSessionToken(token);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(errorEnvelope(), 401, "request-auth")),
    );
    render(<App />);

    expect(
      await screen.findByRole("heading", { name: /Connect to your Personal AI/ }),
    ).toBeVisible();
    expect(readSessionToken()).toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent("saved session");
    expect(document.body.textContent).not.toContain(token);
    expect(document.body.textContent).not.toContain(rawSecret);
  });

  it("preserves a saved session across an outage and reconnects without re-entry", async () => {
    writeSessionToken(token);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("network unavailable");
      }),
    );
    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "Backend unavailable" }),
    ).toBeVisible();
    expect(readSessionToken()).toBe(token);
    expect(document.body.textContent).not.toContain(token);
    expect(screen.getByRole("status")).toHaveTextContent("session is preserved");

    installWorkspaceFetch();
    await userEvent.click(screen.getByRole("button", { name: "Retry connection" }));

    expect(await screen.findByText(user.id)).toBeVisible();
    expect(await screen.findByRole("button", { name: /Local chat/ })).toBeVisible();
    expect(readSessionToken()).toBe(token);
    expect(document.body.textContent).not.toContain(token);
  });

  it("sends an exact prompt, shows pending state, and renders persisted success", async () => {
    writeSessionToken(token);
    const workspace = installWorkspaceFetch();
    render(<App />);

    await userEvent.click(await screen.findByRole("button", { name: /Local chat/ }));
    await screen.findByText("hello");
    await userEvent.type(screen.getByLabelText("Message"), "next");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("answer")).toBeVisible();
    const generationCall = workspace.calls.find(
      (call) => call.url.pathname.endsWith("/messages/generate"),
    );
    expect(JSON.parse(String(generationCall?.init?.body))).toEqual({
      model_id: model.model_id,
      user_message: "next",
    });
    expect(workspace.messageReads()).toBeGreaterThanOrEqual(2);
  });

  it("refreshes history before showing a safe 409 diagnostic", async () => {
    writeSessionToken(token);
    const workspace = installWorkspaceFetch({ generationStatus: 409 });
    render(<App />);

    await userEvent.click(await screen.findByRole("button", { name: /Local chat/ }));
    await screen.findByText("hello");
    const readsBefore = workspace.messageReads();
    await userEvent.type(screen.getByLabelText("Message"), "next");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("conversation changed");
    expect(alert).toHaveTextContent("History was refreshed");
    expect(alert).toHaveTextContent("HTTP 409");
    expect(alert).toHaveTextContent("request-conflict");
    expect(alert).not.toHaveTextContent(rawSecret);
    await waitFor(() => expect(workspace.messageReads()).toBeGreaterThan(readsBefore));
  });

  it("cancels an active request, refreshes history, and fabricates no response", async () => {
    writeSessionToken(token);
    const workspace = installWorkspaceFetch({ generationStatus: "pending" });
    render(<App />);

    await userEvent.click(await screen.findByRole("button", { name: /Local chat/ }));
    await screen.findByText("hello");
    await userEvent.type(screen.getByLabelText("Message"), "cancel this");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Generating");
    const readsBefore = workspace.messageReads();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Request cancelled");
    expect(alert).toHaveTextContent("History was refreshed");
    expect(screen.queryByText("answer")).not.toBeInTheDocument();
    await waitFor(() =>
      expect(workspace.messageReads()).toBeGreaterThan(readsBefore),
    );
  });
});
