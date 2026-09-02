#!/usr/bin/env node

import { readFile, readdir, stat } from "node:fs/promises";
import { resolve } from "node:path";

const MAX_INITIAL_JAVASCRIPT_BYTES = 500 * 1024;
const REQUIRED_LAZY_WORKSPACES = [
  "AgentPanel-",
  "ConnectorPanel-",
  "CreativePanel-",
  "FeatureCatalogPanel-",
  "FinancePanel-",
  "LearningPanel-",
  "MarketingPanel-",
  "MemoryPanel-",
  "SettingsPanel-",
  "ToolPanel-",
  "WorkflowPanel-",
];

const distributionRoot = resolve(process.cwd(), process.argv[2] ?? "dist");
const assetsRoot = resolve(distributionRoot, "assets");
const index = await readFile(resolve(distributionRoot, "index.html"), "utf8");
const initialScript = index.match(
  /<script[^>]+type="module"[^>]+src="\/assets\/([^"/]+\.js)"/,
)?.[1];

if (initialScript === undefined) {
  throw new Error("The production web entry script could not be identified.");
}

const initialSize = (await stat(resolve(assetsRoot, initialScript))).size;
if (initialSize > MAX_INITIAL_JAVASCRIPT_BYTES) {
  throw new Error(
    `Initial web JavaScript is ${initialSize} bytes; the limit is ${MAX_INITIAL_JAVASCRIPT_BYTES}.`,
  );
}

const assets = await readdir(assetsRoot);
for (const prefix of REQUIRED_LAZY_WORKSPACES) {
  if (!assets.some((asset) => asset.startsWith(prefix) && asset.endsWith(".js"))) {
    throw new Error(`The ${prefix.slice(0, -1)} workspace is not emitted on demand.`);
  }
}

process.stdout.write(
  `web bundle validated: ${initialSize} initial bytes; ${REQUIRED_LAZY_WORKSPACES.length} workspaces load on demand\n`,
);
