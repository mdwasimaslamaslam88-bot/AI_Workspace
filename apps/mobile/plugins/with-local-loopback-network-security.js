const { withAndroidManifest, withDangerousMod } = require("expo/config-plugins");
const { copyFile, mkdir } = require("node:fs/promises");
const { join } = require("node:path");

const NETWORK_SECURITY_RESOURCE = "work_station_network_security_config";

function withLocalLoopbackNetworkSecurity(config) {
  config = withAndroidManifest(config, (androidConfig) => {
    const application = androidConfig.modResults.manifest.application?.[0];
    if (application === undefined) {
      throw new Error("The Android manifest is missing its application element.");
    }
    application.$["android:usesCleartextTraffic"] = "false";
    application.$["android:networkSecurityConfig"] = `@xml/${NETWORK_SECURITY_RESOURCE}`;
    return androidConfig;
  });

  return withDangerousMod(config, [
    "android",
    async (androidConfig) => {
      const destinationDirectory = join(
        androidConfig.modRequest.platformProjectRoot,
        "app",
        "src",
        "main",
        "res",
        "xml",
      );
      await mkdir(destinationDirectory, { recursive: true });
      await copyFile(
        join(
          androidConfig.modRequest.projectRoot,
          "resources",
          "android",
          `${NETWORK_SECURITY_RESOURCE}.xml`,
        ),
        join(destinationDirectory, `${NETWORK_SECURITY_RESOURCE}.xml`),
      );
      return androidConfig;
    },
  ]);
}

module.exports = withLocalLoopbackNetworkSecurity;
