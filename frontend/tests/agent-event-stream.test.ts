import { describe, expect, it, vi } from "vitest";

import { ApiClient } from "../src/api/client";
import { token } from "./fixtures";


const runId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";

function event(sequence: number, status: "queued" | "planning" | "completed") {
  return {
    sequence,
    status,
    created_at: `2026-09-02T00:00:0${sequence}Z`,
    step_id: status === "queued" ? null : "execute-goal",
    attempt: status === "queued" ? null : 1,
    agent: status === "queued" ? null : "planner",
    model_id: status === "queued" ? null : `ollama:${"1".repeat(24)}`,
    action: "status",
    detail_sha256: null,
  };
}

describe("mission event stream", () => {
  it("uses authenticated SSE and emits ordered validated lifecycle events", async () => {
    const body = [event(1, "queued"), event(2, "planning"), event(3, "completed")]
      .map((item) => `id: ${item.sequence}\nevent: mission-status\ndata: ${JSON.stringify(item)}\n\n`)
      .join("");
    const fetchImplementation = vi.fn(async (
      _input: URL | RequestInfo,
      _init?: RequestInit,
    ) => {
      void _input;
      void _init;
      return new Response(body, {
        headers: { "Content-Type": "text/event-stream; charset=utf-8" },
      });
    });
    const client = new ApiClient(token, {
      fetchImplementation: fetchImplementation as typeof fetch,
    });
    const received: string[] = [];

    await client.streamAgentRunEvents(
      runId,
      (item) => received.push(item.status),
      new AbortController().signal,
    );

    expect(received).toEqual(["queued", "planning", "completed"]);
    const [url, init] = fetchImplementation.mock.calls[0]!;
    expect(url.toString()).toBe(
      `http://127.0.0.1:8000/api/v1/agent-os/runs/${runId}/events?after=0`,
    );
    expect(new Headers(init?.headers).get("Authorization")).toBe(`Bearer ${token}`);
  });

  it("fails closed on malformed event data", async () => {
    const fetchImplementation = vi.fn(async (
      _input: URL | RequestInfo,
      _init?: RequestInit,
    ) => {
      void _input;
      void _init;
      return new Response(
        "event: mission-status\ndata: {\"sequence\":0}\n\n",
        { headers: { "Content-Type": "text/event-stream" } },
      );
    });
    const client = new ApiClient(token, {
      fetchImplementation: fetchImplementation as typeof fetch,
    });

    await expect(client.streamAgentRunEvents(
      runId,
      vi.fn(),
      new AbortController().signal,
    )).rejects.toThrow("invalid mission event");
  });

  it("backs off and resumes from the last sequence after an early close", async () => {
    vi.useFakeTimers();
    const fetchImplementation = vi
      .fn()
      .mockResolvedValueOnce(new Response(
        `data: ${JSON.stringify(event(1, "queued"))}\n\n`,
        { headers: { "Content-Type": "text/event-stream" } },
      ))
      .mockResolvedValueOnce(new Response(
        `data: ${JSON.stringify(event(2, "completed"))}\n\n`,
        { headers: { "Content-Type": "text/event-stream" } },
      ));
    const client = new ApiClient(token, {
      fetchImplementation: fetchImplementation as typeof fetch,
    });
    const received: string[] = [];

    try {
      const streaming = client.streamAgentRunEvents(
        runId,
        (item) => received.push(item.status),
        new AbortController().signal,
      );
      await vi.advanceTimersByTimeAsync(250);
      await streaming;

      expect(received).toEqual(["queued", "completed"]);
      expect(fetchImplementation).toHaveBeenCalledTimes(2);
      expect(fetchImplementation.mock.calls[1]![0].toString()).toContain("after=1");
    } finally {
      vi.useRealTimers();
    }
  });
});
