import { describe, expect, it, vi } from "vitest";

import { ApiClient } from "../src/api/client";
import { errorEnvelope, jsonResponse, rawSecret, token } from "./fixtures";


class ControlledXhr {
  responseType = "";
  response: unknown = null;
  status = 0;
  body: Document | XMLHttpRequestBodyInit | null = null;
  method = "";
  url = "";
  aborted = false;
  readonly headers = new Map<string, string>();
  readonly upload = { onprogress: null as ((event: ProgressEvent) => void) | null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;

  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }

  setRequestHeader(name: string, value: string) {
    this.headers.set(name, value);
  }

  getResponseHeader() {
    return null;
  }

  send(body: Document | XMLHttpRequestBodyInit | null) {
    this.body = body;
  }

  abort() {
    this.aborted = true;
    this.onabort?.();
  }
}


describe("attachment API client", () => {
  it("cancels XHR upload and permits retry with the caller's same idempotency key", async () => {
    const first = new ControlledXhr();
    const second = new ControlledXhr();
    const requests = [first, second];
    const client = new ApiClient(token, {
      xhrFactory: () => requests.shift() as unknown as XMLHttpRequest,
    });
    const file = new File(["opaque"], "opaque.bin");
    const key = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    const controller = new AbortController();

    const cancelled = client.uploadAsset(file, key, { signal: controller.signal });
    controller.abort();
    await expect(cancelled).rejects.toMatchObject({ kind: "cancelled" });
    expect(first.aborted).toBe(true);

    const retried = client.uploadAsset(file, key);
    expect(first.headers.get("Idempotency-Key")).toBe(key);
    expect(second.headers.get("Idempotency-Key")).toBe(key);
    second.onerror?.();
    await expect(retried).rejects.toMatchObject({ kind: "network" });
  });

  it("downloads and deletes with bearer headers but never URL credentials", async () => {
    const fetchImplementation = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(new Blob(["opaque"]), {
          status: 200,
          headers: { "X-Asset-Media-Type": "application/octet-stream" },
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    const client = new ApiClient(token, {
      fetchImplementation: fetchImplementation as typeof fetch,
    });
    const assetId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";

    await expect(client.downloadAsset(assetId)).resolves.toBeInstanceOf(Blob);
    await expect(client.deleteAsset(assetId)).resolves.toBeUndefined();

    for (const [input, init] of fetchImplementation.mock.calls) {
      expect(input.toString()).toContain(`/api/v1/assets/${assetId}`);
      expect(input.toString()).not.toContain(token);
      expect(new Headers(init?.headers).get("Authorization")).toBe(`Bearer ${token}`);
    }
    expect(fetchImplementation.mock.calls[1]?.[1]?.method).toBe("DELETE");
  });

  it("maps storage failures without reflecting backend details or paths", async () => {
    const client = new ApiClient(token, {
      fetchImplementation: vi.fn(async () =>
        jsonResponse(errorEnvelope(), 503),
      ) as typeof fetch,
    });

    const error = await client
      .downloadAsset("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
      .catch((caught: unknown) => caught);

    expect(error).toMatchObject({
      kind: "unavailable",
      message: "Attachment storage is unavailable.",
    });
    expect(String(error)).not.toContain(rawSecret);
    expect(String(error)).not.toContain(token);
    expect(String(error)).not.toContain("objects/");
  });
});
