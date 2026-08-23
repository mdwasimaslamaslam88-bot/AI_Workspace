import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const config = JSON.parse(await readFile(resolve(root, "src-tauri/tauri.conf.json"), "utf8"));
const platformTargets = Object.fromEntries(
  await Promise.all(
    ["linux", "windows", "macos"].map(async (platform) => {
      const platformConfig = JSON.parse(
        await readFile(resolve(root, `src-tauri/tauri.${platform}.conf.json`), "utf8"),
      );
      return [platform, platformConfig.bundle?.targets];
    }),
  ),
);
const capability = JSON.parse(
  await readFile(resolve(root, "src-tauri/capabilities/main.json"), "utf8"),
);

if (config.productName !== "WORK STATION") throw new Error("desktop product name mismatch");
if (config.identifier !== "com.workstation.personalai") throw new Error("desktop identifier mismatch");
if (config.build.frontendDist !== "../../../frontend/dist") {
  throw new Error("desktop must package the canonical web build");
}
if (config.app.windows.some((window) => window.dragDropEnabled !== false)) {
  throw new Error("desktop must use path-free HTML5 file drops");
}
if (config.bundle.targets !== undefined) {
  throw new Error("desktop bundle targets must be selected per platform");
}
const expectedTargets = {
  linux: ["deb"],
  windows: ["nsis"],
  macos: ["app", "dmg"],
};
for (const [platform, expected] of Object.entries(expectedTargets)) {
  const actual = platformTargets[platform];
  if (!Array.isArray(actual) || actual.join() !== expected.join()) {
    throw new Error(`desktop ${platform} bundle targets mismatch`);
  }
}
if (config.app.security.csp === null || config.app.security.csp.includes("unsafe-eval")) {
  throw new Error("desktop CSP is missing or unsafe");
}
if (!Array.isArray(capability.windows) || capability.windows.join() !== "main") {
  throw new Error("desktop permissions must be scoped to the main window");
}
const forbidden = ["shell", "process", "fs:default", "http:default"];
if (capability.permissions.some((permission) => forbidden.some((item) => permission.includes(item)))) {
  throw new Error("desktop capability grants a forbidden broad permission");
}

console.log("desktop configuration: valid least-privilege canonical shell");
