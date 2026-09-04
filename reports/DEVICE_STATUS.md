# Step 7 Device and Remote Ecosystem Status

Status labels separate local build/runtime evidence from physical-device and
external-provider evidence. No physical-device, public-network, store, or push
delivery result is inferred from a package build.

| Capability | Status | Evidence or exact boundary |
|---|---|---|
| Ubuntu desktop | DEVICE PASS | Production Tauri binary and AppImage opened real X11 windows and closed cleanly on the current Ubuntu workstation |
| Web/PWA | RUNTIME PASS | Chromium install/navigation/auth/chat/cache-isolation/logout E2E and production service-worker/manifest validation |
| Private remote gateway | RUNTIME PASS | Active Tailscale Serve route, TLS, readiness, PWA/CSP, uniform unauthenticated denial, Funnel disabled, and loopback-only backend |
| Desktop to mobile continuation | RUNTIME PASS | Independent desktop/mobile owner sessions used the real private HTTPS route; mobile session observed and completed the desktop-created mission with verified result |
| Mobile to desktop continuation | RUNTIME PASS | Independent mobile/desktop owner sessions used the real private HTTPS route; desktop session observed and completed the mobile-created mission with verified result |
| Device registration and trust | LOCAL/RUNTIME PASS | Owner-scoped session issue, label, list, current-session detection, targeted revoke, logout, and post-revocation denial passed |
| Remote authenticated session | RUNTIME PASS | Temporary owner provisioned through the active private gateway; both bearer sessions authenticated; exact test data was removed and database counts returned to baseline |
| Windows desktop | BUILD/NATIVE PASS; trust boundary | Native x64 PE/NSIS build, scan and launch run `33897323310` succeeded for exact commit `7fafcf8`; trusted publisher certificate and owner physical-machine acceptance remain external |
| macOS desktop | BUILD/NATIVE PASS; trust boundary | Native arm64 app ZIP/DMG build, strict ad-hoc signing, scan and launch run `33897326738` succeeded for exact commit `7fafcf8`; Developer ID/notarization and owner physical-machine acceptance remain external |
| Android | EMULATOR DEVICE PASS; ARM PHYSICAL BLOCKED | The preserved Phase 11 Android 16/API 36 x86_64 emulator run passed install, launch and Mission Control navigation. The fresh ARM64 owner-sideload APK passed build, ABI, signing, alignment, path and secret scans; no physical Android was attached in Step 7 |
| iOS | STATIC PASS; EXTERNAL BLOCKED | Expo static export passed; Apple hardware, account, signing, and physical-device validation remain external |
| Notification routing | LOCAL PASS; PROVIDER BLOCKED | Local notification contracts pass; remote push delivery needs an authorized FCM/APNs/EAS provider and registered physical device |
| Public internet exposure | DISABLED | No public Funnel or router forwarding is enabled; owner devices must be enrolled in the private tailnet |

## Step 7 validation

- Backend authentication/session/workflow/remote-harness tests: **109 passed, 4 skipped**.
- Web session/platform tests: **30 passed**.
- Mobile session, API, deep-link, network, notification, and workflow tests:
  **23 passed** in the focused gate and **64 passed** in the canonical gate.
- Remote gateway configuration and private-target guards: **passed**.
- Authenticated production remote smoke: **passed**, including exact cleanup.
- Security gate: **passed** with no critical/high findings or secret leak.
- Canonical backend **3,003 passed, 53 skipped**; web **201 passed**; PostgreSQL
  **53 passed** at migration `0021` with no drift.
- Native Android build and artifact validation: **passed**. Physical-device
  execution remains `DEVICE_BLOCKED` because `adb devices -l` was empty.
- The prior Android 16/API 36 x86_64 emulator device-run evidence remains valid
  and is not represented as ARM64 physical-device acceptance.
- Native Windows/macOS exact-commit hosted build, scan and launch gates:
  **passed**.

The local userspace Tailscale daemon does not install host MagicDNS routing.
The production smoke therefore uses Tailscale's own userspace TCP transport to
the same private Serve endpoint while retaining the real TLS hostname/SNI. An
enrolled remote device uses the normal tailnet path.

## Owner/external actions still required

- Enroll each real phone/desktop in the owner's tailnet and keep the tailnet
  access policy restricted to those identities/devices.
- Install and launch the ARM64 APK on an owner-controlled physical Android
  device before claiming physical-device acceptance for that release ABI.
- Configure an authorized push provider and device registration before
  claiming remote notification delivery.
- Supply trusted Windows and Apple signing identities for public distribution;
  Apple notarization additionally needs an Apple developer account/service.

The fresh APK is stored at
`/home/md-wasim/AI_Workspace_Data/releases/step7-7fafcf8/Work_Station_Android_ARM64.apk`
with SHA-256
`c5dd4a4101e500abc0d4274eff1f6020b37e5b199b09d573b0f07f3b72abd2f4`.
