import assert from "node:assert/strict";
import { existsSync, readFileSync, rmdirSync, unlinkSync } from "node:fs";
import { join } from "node:path";

import { chromium, request } from "playwright";

function requiredEnvironment(name) {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`The ${name} test setting is required.`);
  return value;
}

function exactHttpOrigin(value, name) {
  const parsed = new URL(value);
  assert.ok(
    ["http:", "https:"].includes(parsed.protocol) &&
      parsed.username === "" &&
      parsed.password === "" &&
      parsed.pathname === "/" &&
      parsed.search === "" &&
      parsed.hash === "",
    `${name} must be an exact HTTP origin.`,
  );
  return parsed.origin;
}

async function waitForNonemptyAssistant(page, previousCount) {
  await page.waitForFunction(
    (count) => {
      const messages = document.querySelectorAll(
        ".message-assistant .markdown-body",
      );
      const newest = messages.item(messages.length - 1);
      return messages.length > count && (newest.textContent?.trim().length ?? 0) > 0;
    },
    previousCount,
    { timeout: 120_000 },
  );
}

const apiOrigin = exactHttpOrigin(
  requiredEnvironment("WORK_STATION_E2E_API_ORIGIN"),
  "WORK_STATION_E2E_API_ORIGIN",
);
const webOrigin = exactHttpOrigin(
  requiredEnvironment("WORK_STATION_E2E_WEB_ORIGIN"),
  "WORK_STATION_E2E_WEB_ORIGIN",
);
const filesystemRoot = requiredEnvironment("WORK_STATION_E2E_FILESYSTEM_ROOT");
assert.ok(filesystemRoot.startsWith("/"), "The filesystem E2E root must be absolute.");
const authMode = process.env.WORK_STATION_E2E_AUTH_MODE?.trim() || "provision";
const testDex = process.env.WORK_STATION_E2E_DEX === "true";
assert.ok(
  ["provision", "existing-session"].includes(authMode),
  "WORK_STATION_E2E_AUTH_MODE must be provision or existing-session.",
);
let pipedCredential = readFileSync(0, "utf8").trim();
assert.ok(pipedCredential.length > 0, "The piped credential is required.");

const apiRequest = await request.newContext({ baseURL: `${apiOrigin}/` });
let accessToken = "";
let ownerId = "";
let createdConversationId = "";
let createdFile = "";
let browser;

try {
  if (authMode === "provision") {
    const provisioningResponse = await apiRequest.post("api/v1/users", {
      data: {},
      headers: { "X-User-Provisioning-Token": pipedCredential },
    });
    assert.equal(provisioningResponse.status(), 201, "Owner provisioning failed.");
    assert.match(
      provisioningResponse.headers()["cache-control"] ?? "",
      /(?:^|,)\s*no-store\s*(?:,|$)/i,
      "Provisioning must be non-cacheable.",
    );
    const provisioned = await provisioningResponse.json();
    assert.equal(provisioned.token_type, "bearer", "Unexpected token type.");
    assert.equal(
      typeof provisioned.access_token,
      "string",
      "Provisioning did not return an access token.",
    );
    assert.ok(provisioned.access_token.length > 0, "The access token was empty.");
    accessToken = provisioned.access_token;
    ownerId = provisioned.id;
  } else {
    accessToken = pipedCredential;
  }

  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    serviceWorkers: "allow",
  });
  const page = await context.newPage();
  page.setDefaultTimeout(30_000);

  const documentResponse = await page.goto(webOrigin, {
    waitUntil: "domcontentloaded",
  });
  assert.equal(documentResponse?.status(), 200, "The compiled PWA did not load.");
  assert.match(
    documentResponse?.headers()["content-security-policy"] ?? "",
    /default-src 'self'/,
    "The PWA response is missing its restrictive content policy.",
  );
  await page
    .getByRole("heading", { name: "Connect to your Personal AI" })
    .waitFor();

  const manifestHref = await page
    .locator('link[rel="manifest"]')
    .getAttribute("href");
  assert.ok(manifestHref, "The PWA manifest link is missing.");
  const manifestResponse = await context.request.get(
    new URL(manifestHref, webOrigin).href,
  );
  assert.equal(manifestResponse.status(), 200, "The PWA manifest is unavailable.");
  const manifest = await manifestResponse.json();
  assert.equal(manifest.name, "WORK STATION", "Unexpected PWA identity.");
  assert.equal(manifest.display, "standalone", "The PWA is not installable.");

  await page.evaluate(async () => {
    if (!("serviceWorker" in navigator)) {
      throw new Error("Service workers are unavailable.");
    }
    await Promise.race([
      navigator.serviceWorker.ready,
      new Promise((_, reject) =>
        window.setTimeout(
          () => reject(new Error("Service worker registration timed out.")),
          20_000,
        ),
      ),
    ]);
  });
  await page.reload({ waitUntil: "domcontentloaded" });
  await page
    .getByRole("heading", { name: "Connect to your Personal AI" })
    .waitFor();
  assert.equal(
    await page.evaluate(() => navigator.serviceWorker.controller !== null),
    true,
    "The installed service worker did not control the PWA.",
  );

  const bearerInput = page.getByLabel("Bearer token");
  assert.equal(
    await bearerInput.getAttribute("type"),
    "password",
    "The bearer credential input must remain masked.",
  );

  const currentUserResponse = page.waitForResponse(
    (response) => {
      const responseUrl = new URL(response.url());
      return (
        response.request().method() === "GET" &&
        responseUrl.origin === apiOrigin &&
        responseUrl.pathname === "/api/v1/users/me"
      );
    },
  );
  await bearerInput.fill(accessToken);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  const currentUserHttpResponse = await currentUserResponse;
  assert.equal(
    currentUserHttpResponse.status(),
    200,
    "Browser current-user resolution failed.",
  );
  const currentUser = await currentUserHttpResponse.json();
  if (ownerId) {
    assert.equal(currentUser.id, ownerId, "Provisioned owner identity changed.");
  } else {
    ownerId = currentUser.id;
  }
  await page.getByLabel("Connection status").waitFor();
  assert.equal(
    (await page.getByLabel("Connection status").innerText()).trim(),
    "Connected",
    "The workspace did not enter its connected state.",
  );
  await page.getByRole("heading", { name: "WORK STATION" }).waitFor();
  assert.equal(
    await page.evaluate(
      (token) =>
        sessionStorage.getItem("work-station.bearer-token") === token,
      accessToken,
    ),
    true,
    "The bearer credential was not retained in session-only storage.",
  );

  const credentialExposure = await page.evaluate((token) => {
    const resourceUrls = performance
      .getEntriesByType("resource")
      .map((entry) => entry.name);
    return {
      body: document.body.innerText.includes(token),
      localStorage: Object.keys(localStorage).some((key) =>
        localStorage.getItem(key)?.includes(token),
      ),
      url:
        window.location.href.includes(token) ||
        resourceUrls.some((value) => value.includes(token)),
    };
  }, accessToken);
  assert.deepEqual(
    credentialExposure,
    { body: false, localStorage: false, url: false },
    "A bearer credential escaped its session boundary.",
  );

  const featureRegistryResponse = page.waitForResponse((response) => {
    const responseUrl = new URL(response.url());
    return (
      response.request().method() === "GET" &&
      responseUrl.origin === apiOrigin &&
      responseUrl.pathname === "/api/v1/features"
    );
  });
  await page.getByRole("button", { name: /Universal Workspace/ }).click();
  const featureRegistryHttpResponse = await featureRegistryResponse;
  assert.equal(
    featureRegistryHttpResponse.status(),
    200,
    "Authenticated feature registry loading failed.",
  );
  const featureRegistryPayload = await featureRegistryHttpResponse.json();
  await page.getByRole("heading", { name: "Universal Workspace" }).waitFor();
  assert.ok(
    (await page.getByText(/registered product capabilities/).innerText()).includes(
      `${featureRegistryPayload.count} registered product capabilities`,
    ),
    "The complete feature registry was not exposed through the workspace catalog.",
  );
  assert.equal(
    await page.getByRole("button", { name: "Connect service" }).first().isDisabled(),
    true,
    "An external-service capability was presented as executable.",
  );
  await page.getByRole("button", { name: "Close", exact: true }).click();

  const communicationCapabilitiesResponse = page.waitForResponse((response) => {
    const responseUrl = new URL(response.url());
    return (
      response.request().method() === "GET" &&
      responseUrl.origin === apiOrigin &&
      responseUrl.pathname === "/api/v1/communications/capabilities"
    );
  });
  await page.getByRole("button", { name: "Calls", exact: true }).click();
  assert.equal(
    (await communicationCapabilitiesResponse).status(),
    200,
    "Authenticated communication capability loading failed.",
  );
  await page.getByRole("heading", { name: "Calls & callbacks" }).waitFor();
  await page
    .getByText("No health-verified communication provider is configured.")
    .waitFor();
  assert.equal(
    await page.getByRole("button", { name: "Place verified call" }).isDisabled(),
    true,
    "An unconfigured phone provider was presented as executable.",
  );
  await page
    .getByRole("button", { name: "Configure communication gateway" })
    .click();
  await page.getByRole("heading", { name: "Connected apps" }).waitFor();
  await page.getByRole("button", { name: "Close", exact: true }).click();

  await page.getByRole("button", { name: "New conversation" }).click();
  await page.getByRole("heading", { name: "Start with a prompt" }).waitFor();
  await page.getByLabel("Title (optional)").fill("Browser release smoke");
  await page
    .getByLabel("Your first message")
    .fill("Reply with one short sentence confirming the browser smoke is ready.");

  const firstAssistantCount = await page
    .locator(".message-assistant .markdown-body")
    .count();
  const conversationCreated = page.waitForResponse(
    (response) => {
      const responseUrl = new URL(response.url());
      return (
        response.request().method() === "POST" &&
        responseUrl.origin === apiOrigin &&
        responseUrl.pathname === "/api/v1/conversations"
      );
    },
    { timeout: 120_000 },
  );
  const firstGeneration = page.waitForResponse(
    (response) => {
      const responseUrl = new URL(response.url());
      return (
        response.request().method() === "POST" &&
        responseUrl.origin === apiOrigin &&
        responseUrl.pathname.endsWith("/messages/generate")
      );
    },
    { timeout: 120_000 },
  );
  await page
    .getByRole("button", { name: "Create and generate" })
    .click();
  const conversationCreatedResponse = await conversationCreated;
  assert.equal(
    conversationCreatedResponse.status(),
    201,
    "Browser conversation creation failed.",
  );
  createdConversationId = (await conversationCreatedResponse.json()).id;
  assert.equal(
    (await firstGeneration).status(),
    201,
    "Initial browser generation failed.",
  );
  await waitForNonemptyAssistant(page, firstAssistantCount);

  const secondAssistantCount = await page
    .locator(".message-assistant .markdown-body")
    .count();
  await page
    .getByRole("textbox", { name: "Message", exact: true })
    .fill("Reply with one different short sentence confirming local chat works.");
  const secondGeneration = page.waitForResponse(
    (response) => {
      const responseUrl = new URL(response.url());
      return (
        response.request().method() === "POST" &&
        responseUrl.origin === apiOrigin &&
        responseUrl.pathname.endsWith("/messages/generate")
      );
    },
    { timeout: 120_000 },
  );
  await page.getByRole("button", { name: "Send", exact: true }).click();
  assert.equal(
    (await secondGeneration).status(),
    201,
    "Follow-up browser generation failed.",
  );
  await waitForNonemptyAssistant(page, secondAssistantCount);

  const toolAssistantCount = await page
    .locator(".message-assistant .markdown-body")
    .count();
  await page
    .getByRole("textbox", { name: "Message", exact: true })
    .fill("Create AI_OS_REAL_TEST.txt with:\nAI OS REAL EXECUTION VERIFIED");
  const toolGenerationPromise = page.waitForResponse(
    (response) => {
      const responseUrl = new URL(response.url());
      return (
        response.request().method() === "POST" &&
        responseUrl.origin === apiOrigin &&
        responseUrl.pathname.endsWith("/messages/generate")
      );
    },
    { timeout: 180_000 },
  );
  await page.getByRole("button", { name: "Send", exact: true }).click();
  const toolGenerationResponse = await toolGenerationPromise;
  assert.equal(
    toolGenerationResponse.status(),
    201,
    "Authenticated browser tool generation failed.",
  );
  const toolGeneration = await toolGenerationResponse.json();
  assert.equal(
    toolGeneration.execution?.status,
    "completed",
    "The chat tool path did not return completed execution evidence.",
  );
  assert.equal(
    toolGeneration.execution?.states.at(-1),
    "done",
    "The chat tool path reached Done without a completed trace.",
  );
  assert.deepEqual(
    toolGeneration.execution?.receipts.map((receipt) => receipt.tool),
    ["filesystem.write", "filesystem.exists", "filesystem.read"],
    "The chat tool path omitted write/read-back audit receipts.",
  );
  await waitForNonemptyAssistant(page, toolAssistantCount);
  const executionRegion = page.getByRole("region", {
    name: "AI OS tool execution",
  });
  await executionRegion.waitFor();
  assert.match(await executionRegion.innerText(), /Verified/i);
  assert.match(await executionRegion.innerText(), /AI_OS_REAL_TEST\.txt/);
  assert.match(await executionRegion.innerText(), /exact read back passed/i);

  if (testDex) {
    const dexAssistantCount = await page
      .locator(".message-assistant .markdown-body")
      .count();
    await page
      .getByRole("textbox", { name: "Message", exact: true })
      .fill("Ask DEX to inspect a harmless repository condition and report its evidence.");
    const dexGenerationPromise = page.waitForResponse(
      (response) => {
        const responseUrl = new URL(response.url());
        return (
          response.request().method() === "POST" &&
          responseUrl.origin === apiOrigin &&
          responseUrl.pathname.endsWith("/messages/generate")
        );
      },
      { timeout: 300_000 },
    );
    await page.getByRole("button", { name: "Send", exact: true }).click();
    const dexGenerationResponse = await dexGenerationPromise;
    assert.equal(
      dexGenerationResponse.status(),
      201,
      "Authenticated browser DEX generation failed.",
    );
    const dexGeneration = await dexGenerationResponse.json();
    assert.equal(
      dexGeneration.execution?.status,
      "completed",
      "The browser DEX path did not return a completed verified trace.",
    );
    assert.deepEqual(
      dexGeneration.execution?.receipts.map((receipt) => [
        receipt.tool,
        receipt.verification,
      ]),
      [["dex.delegate", "dex_result_verified"]],
      "The browser DEX path omitted its verified audit receipt.",
    );
    for (const state of ["asking_dex", "dex_working", "verifying_dex", "done"]) {
      assert.ok(
        dexGeneration.execution?.states.includes(state),
        `The browser DEX trace omitted ${state}.`,
      );
    }
    await waitForNonemptyAssistant(page, dexAssistantCount);
    assert.match(await executionRegion.innerText(), /asking dex/i);
    assert.match(await executionRegion.innerText(), /dex working/i);
    assert.match(await executionRegion.innerText(), /verifying dex/i);
    assert.match(await executionRegion.innerText(), /dex result verified/i);
  }

  const ownerWorkspace = join(filesystemRoot, ownerId);
  createdFile = join(ownerWorkspace, "AI_OS_REAL_TEST.txt");
  assert.equal(existsSync(createdFile), true, "The real owner file was not created.");
  assert.equal(
    readFileSync(createdFile, "utf8"),
    "AI OS REAL EXECUTION VERIFIED",
    "The real owner file failed exact content verification.",
  );
  const auditResponse = await apiRequest.get("api/v1/tools/executions", {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  assert.equal(auditResponse.status(), 200, "Tool audit retrieval failed.");
  const auditItems = (await auditResponse.json()).items;
  const relevantAudits = auditItems.filter((item) =>
    ["filesystem.write", "filesystem.exists", "filesystem.read"].includes(
      item.tool_name,
    ),
  );
  assert.deepEqual(
    relevantAudits.slice(0, 3).map((item) => item.tool_name).sort(),
    ["filesystem.exists", "filesystem.read", "filesystem.write"],
    "Durable filesystem audit evidence is incomplete.",
  );
  assert.equal(
    relevantAudits.some((item) =>
      JSON.stringify(item).includes("AI OS REAL EXECUTION VERIFIED"),
    ),
    false,
    "Filesystem content leaked into audit payloads.",
  );
  unlinkSync(createdFile);
  rmdirSync(ownerWorkspace);

  assert.equal(
    await page.evaluate(async () => {
      const cacheNames = await caches.keys();
      for (const cacheName of cacheNames) {
        const cachedRequests = await (await caches.open(cacheName)).keys();
        if (
          cachedRequests.some(
            (cachedRequest) =>
              new URL(cachedRequest.url).pathname.startsWith("/api/"),
          )
        ) {
          return false;
        }
      }
      return true;
    }),
    true,
    "The PWA cached a private API response.",
  );

  const deleteConversationResponse = await apiRequest.delete(
    `api/v1/conversations/${createdConversationId}`,
    { headers: { Authorization: `Bearer ${accessToken}` } },
  );
  assert.equal(
    deleteConversationResponse.status(),
    204,
    "The browser smoke conversation was not cleaned up.",
  );
  createdConversationId = "";
  await page.getByRole("button", { name: "Logout" }).click();
  await page
    .getByRole("heading", { name: "Connect to your Personal AI" })
    .waitFor();
  assert.equal(
    await page.evaluate(
      () => sessionStorage.getItem("work-station.bearer-token") === null,
    ),
    true,
    "Logout did not clear the browser session credential.",
  );
  await context.close();
  console.log(
    "browser/PWA E2E: install, connect, authenticated chat tool execution, real file read-back, audit, cleanup, cache isolation, and logout passed",
  );
} finally {
  if (createdConversationId && accessToken) {
    await apiRequest.delete(`api/v1/conversations/${createdConversationId}`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    }).catch(() => undefined);
  }
  if (createdFile && existsSync(createdFile)) {
    unlinkSync(createdFile);
    const ownerWorkspace = join(filesystemRoot, ownerId);
    try {
      rmdirSync(ownerWorkspace);
    } catch {
      // Preserve non-test owner files if the workspace is not empty.
    }
  }
  accessToken = "";
  pipedCredential = "";
  await apiRequest.dispose();
  await browser?.close();
}
