import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const mobileRoot = resolve(import.meta.dirname, "..");

describe("Android network security", () => {
  it("permits local HTTP only on explicit loopback hosts", () => {
    const config = readFileSync(
      resolve(
        mobileRoot,
        "resources/android/work_station_network_security_config.xml",
      ),
      "utf8",
    );

    expect(config).toContain('<base-config cleartextTrafficPermitted="false"');
    expect(config).toContain('<domain-config cleartextTrafficPermitted="true"');
    expect(config).toContain(">127.0.0.1</domain>");
    expect(config).toContain(">localhost</domain>");
    expect(config).not.toMatch(/includeSubdomains="true"|\*|0\.0\.0\.0/);
  });

  it("registers the native policy plugin in Expo configuration", () => {
    const app = JSON.parse(
      readFileSync(resolve(mobileRoot, "app.json"), "utf8"),
    ).expo;

    expect(app.plugins).toContain("./plugins/with-local-loopback-network-security");
  });
});
