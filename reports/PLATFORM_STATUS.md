# Step 7 Platform Status

| Platform | Build | Runtime/launch evidence | Distribution trust | Status |
|---|---|---|---|---|
| Ubuntu Desktop | x86-64 Tauri AppImage and DEB | Current production binary and AppImage opened real X11 windows and closed cleanly | Owner-sideload | DEVICE PASS |
| Windows Desktop | x64 standalone PE and NSIS installer | Native run `33897323310` built exact commit `7fafcf8`, inspected/extracted NSIS, scanned artifacts and kept the app process alive | Unsigned; trusted publisher certificate and owner physical-machine acceptance are external | NATIVE PASS with trust boundary |
| macOS Desktop | arm64 `.app` ZIP and DMG | Native run `33897326738` built exact commit `7fafcf8`, verified bundle/DMG, strict ad-hoc signature, scans and process launch | Developer ID/notarization/stapling and owner physical-machine acceptance require owner resources | NATIVE PASS with trust boundary |
| Android | ARM64 owner-sideload APK plus preserved x86_64 emulator evidence | Fresh native release build passed ABI, signature, alignment, path and secret scans. Phase 11 Android 16/API 36 x86_64 install/launch/Mission Control evidence remains valid; no physical ARM device was attached in Step 7 | Standard debug certificate; physical ARM acceptance and owner store signing remain external | EMULATOR DEVICE PASS; ARM DEVICE BLOCKED |
| Web/PWA | Production Vite bundle and deterministic archive | Chromium install/navigation/auth/chat/cache-isolation/logout E2E, service worker and manifest passed | Private authenticated self-hosting | RUNTIME PASS |
| iOS companion source | Expo static iOS export | Static export passed; no native `.ipa` or physical-device run | Apple hardware, account, provisioning, signing and physical device required | STATIC PASS; native external boundary |

The current web entry is 489,282 bytes before compression and remains below its
500 KiB release guard. Eleven feature workspaces load on demand. Desktop,
mobile and web share the authenticated backend contracts, missions, memory,
permissions and feature authority.

## Android device detail

`adb devices -l` returned no attached device during Step 7. The generated
ARM64 package is therefore a validated build artifact, not physical-device
runtime evidence. Physical install, permission and crash-recovery acceptance
remains an explicit owner/device boundary. The prior Phase 11 Android 16/API
36 x86_64 emulator run remains valid device evidence: the package installed,
`MainActivity` remained top-resumed, and Mission Control navigation passed.

## Artifact authority

Step 7 Android release root:
`/home/md-wasim/AI_Workspace_Data/releases/step7-7fafcf8`

The ARM64 APK SHA-256 is
`c5dd4a4101e500abc0d4274eff1f6020b37e5b199b09d573b0f07f3b72abd2f4`.
Windows and macOS native evidence is linked from
`reports/STEP7_REMOTE_DEVICES.md`; those hosted jobs built and launched exact
commit `7fafcf800be66171b64b136b7a12ee8613205201`.
