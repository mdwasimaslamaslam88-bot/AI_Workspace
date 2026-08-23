import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const app = JSON.parse(await readFile(resolve(root, "app.json"), "utf8")).expo;
const eas = JSON.parse(await readFile(resolve(root, "eas.json"), "utf8"));

if (app.name !== "WORK STATION" || app.slug !== "work-station") {
  throw new Error("mobile product identity mismatch");
}
if (
  app.scheme !== "work-station" ||
  app.android?.package !== "com.workstation.personalai" ||
  app.ios?.bundleIdentifier !== "com.workstation.personalai"
) {
  throw new Error("mobile package identity or deep-link scheme mismatch");
}
if (app.ios.supportsTablet !== true) {
  throw new Error("mobile iPad support is required");
}
if (
  !app.android.permissions.includes("CAMERA") ||
  !app.android.permissions.includes("RECORD_AUDIO")
) {
  throw new Error("mobile camera and microphone permissions are incomplete");
}
const pluginNames = app.plugins.map((plugin) =>
  Array.isArray(plugin) ? plugin[0] : plugin,
);
for (const required of [
  "expo-router",
  "expo-secure-store",
  "expo-image-picker",
  "expo-notifications",
  "expo-splash-screen",
  "expo-audio",
]) {
  if (!pluginNames.includes(required)) {
    throw new Error(`mobile plugin is missing: ${required}`);
  }
}
if (eas.build?.production === undefined || eas.submit?.production === undefined) {
  throw new Error("mobile production build and submission profiles are required");
}

console.log("mobile configuration: valid Android/iOS private client");
