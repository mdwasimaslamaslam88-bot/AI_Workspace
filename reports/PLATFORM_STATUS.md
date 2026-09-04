# Final Platform Status

All artifacts below were built and validated from application source commit
`07f4976a801c566a2434c9b802340c5fbb4ea2c1`.

| Platform | Evidence | Distribution boundary | Status |
|---|---|---|---|
| Ubuntu x86-64 | Tauri binary, AppImage and DEB built; binary and AppImage opened real X11 windows and closed cleanly | Owner-sideload | DEVICE PASS |
| Windows x64 | Native run `33910594358` passed source/web/desktop tests, PE/NSIS build, archive/secret scan and process launch | Trusted publisher certificate and owner-machine acceptance | NATIVE PASS; OWNER TRUST BOUNDARY |
| macOS arm64 | Native run `33910594428` passed source/web/desktop tests, app/DMG build, strict ad-hoc signing, archive/secret scan and process launch | Developer ID, notarization/stapling and owner-machine acceptance | NATIVE PASS; OWNER TRUST BOUNDARY |
| Android ARM64 | Fresh 532-task release build passed ABI, identity, v2/v3 signature, alignment, path and secret checks | Standard debug certificate; physical ARM install and owner release signing/store | BUILD PASS; DEVICE BLOCKED |
| Web/PWA | Production archive, manifest/service worker and compiled Chromium install/auth/chat/cache-isolation/logout E2E passed | Private authenticated deployment | RUNTIME PASS |
| iOS | Shared Expo source, typecheck and static export passed | Apple hardware, account, provisioning, signing and physical device | STATIC PASS; EXTERNAL BLOCKED |

Web's 503,271-byte entry remains within the 512,000-byte release guard and 11
workspaces load lazily. Web, desktop and mobile share the authenticated backend,
mission, memory, permission and registry contracts.

## Release artifacts

Root: `/home/md-wasim/AI_Workspace_Data/releases/final-07f4976`

| Artifact | SHA-256 |
|---|---|
| `Work_Station_Ubuntu.AppImage` | `8df47eb3c88abb68693e3213503e08502866c3d68788f4cfa172173aeae78bc6` |
| `Work_Station_Ubuntu.deb` | `f3f63210c75cdc7822f46228c780f55224f124f41061f916a87e314692105dfb` |
| `Work_Station_Windows_Setup.exe` | `ea00723994e7b092e4054b2967361f3d1d5989e8f6acd160ac428f87202f8ffb` |
| `Work_Station_Windows.exe` | `3e4d61f4e7159793be25bfde7b8afa50bee8e4623bcbcb61dc55f91e02eb033d` |
| `Work_Station_macOS.app.zip` | `b03c050c1b3a63468f0b98acf5307f233627e7e2e3cfc8b696dcaf925cd958e4` |
| `Work_Station_macOS.dmg` | `43725836d5fea98c7fb276cca181bbaacb1c297b6e6e38a967dd56add45ce7dd` |
| `Work_Station_Android_ARM64.apk` | `ff17a3deef47bcaa0ac76107866b4407e46f6b1f354c18a6a35e34e6df32daa1` |
| `Work_Station_Web_PWA.tar.gz` | `b7d2353f4f3c0e1a066693fb60b332065d5024a40beb2a61e26449fcff6d8c2c` |
