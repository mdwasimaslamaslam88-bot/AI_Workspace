import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

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
const provisioningToken = readFileSync(0, "utf8").trim();
assert.ok(provisioningToken.length > 0, "The piped provisioning token is required.");

const apiRequest = await request.newContext({ baseURL: `${apiOrigin}/` });
let accessToken = "";
let browser;

try {
  const provisioningResponse = await apiRequest.post("api/v1/users", {
    data: {},
    headers: { "X-User-Provisioning-Token": provisioningToken },
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
  assert.equal(
    (await currentUserResponse).status(),
    200,
    "Browser current-user resolution failed.",
  );
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
  assert.equal(
    (await featureRegistryResponse).status(),
    200,
    "Authenticated feature registry loading failed.",
  );
  await page.getByRole("heading", { name: "Universal Workspace" }).waitFor();
  assert.match(
    await page.getByText(/registered product capabilities/).innerText(),
    /245 registered product capabilities/,
    "The complete feature registry was not exposed through the workspace catalog.",
  );
  assert.equal(
    await page.getByRole("button", { name: "Documented boundary" }).first().isDisabled(),
    true,
    "A planned capability was presented as executable.",
  );
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
  assert.equal(
    (await conversationCreated).status(),
    201,
    "Browser conversation creation failed.",
  );
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
    "browser/PWA E2E: install, connect, feature registry, current user, conversation, chat, cache isolation, and logout passed",
  );
} finally {
  accessToken = "";
  await apiRequest.dispose();
  await browser?.close();
}
