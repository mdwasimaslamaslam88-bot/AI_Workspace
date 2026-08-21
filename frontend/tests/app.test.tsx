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
  rawSecret,
  token,
  user,
} from "./fixtures";

function installWorkspaceFetch(options: {
  generationStatus?: 201 | 409 | "pending";
} = {}) {
  let generated = false;
  let messageReads = 0;
  const calls: Array<{ url: URL; init: RequestInit | undefined }> = [];
  const fetchMock = vi.fn(async (input: URL | RequestInfo, init?: RequestInit) => {
    const url = new URL(input.toString());
    calls.push({ url, init });

    if (url.pathname === "/api/v1/users/me") return jsonResponse(user);
    if (url.pathname === "/api/v1/ai/models") {
      return jsonResponse({ items: [model] });
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
