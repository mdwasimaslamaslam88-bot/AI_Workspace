import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const workspaceRoot = resolve(root, "../..");
const app = JSON.parse(await readFile(resolve(root, "app.json"), "utf8")).expo;
const eas = JSON.parse(await readFile(resolve(root, "eas.json"), "utf8"));
const mobilePackage = JSON.parse(await readFile(resolve(root, "package.json"), "utf8"));
const webPackage = JSON.parse(
  await readFile(resolve(workspaceRoot, "frontend/package.json"), "utf8"),
);
const workspacePackage = JSON.parse(
  await readFile(resolve(workspaceRoot, "package.json"), "utf8"),
);
const packageLock = JSON.parse(
  await readFile(resolve(workspaceRoot, "package-lock.json"), "utf8"),
);
const expoCompatibility = JSON.parse(
  await readFile(
    resolve(workspaceRoot, "node_modules/expo/bundledNativeModules.json"),
    "utf8",
  ),
);

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
  "./plugins/with-local-loopback-network-security",
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

for (const dependency of ["react", "react-dom"]) {
  const expected = expoCompatibility[dependency];
  if (
    typeof expected !== "string" ||
    mobilePackage.dependencies?.[dependency] !== expected ||
    webPackage.dependencies?.[dependency] !== expected ||
    workspacePackage.devDependencies?.[dependency] !== expected
  ) {
    throw new Error(`${dependency} must match the Expo native compatibility version`);
  }
  const suffix = `/node_modules/${dependency}`;
  const locked = Object.entries(packageLock.packages).filter(
    ([location]) => location === `node_modules/${dependency}` || location.endsWith(suffix),
  );
  if (locked.length !== 1 || locked[0][1]?.version !== expected) {
    throw new Error(`${dependency} must have exactly one Expo-compatible lockfile install`);
  }
  const installed = JSON.parse(
    await readFile(resolve(workspaceRoot, `node_modules/${dependency}/package.json`), "utf8"),
  );
  if (installed.version !== expected) {
    throw new Error(`${dependency} installed version does not match Expo`);
  }
}

console.log("mobile configuration: valid Android/iOS private client with one Expo-compatible React runtime");
