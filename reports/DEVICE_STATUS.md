# Phase H Device and Remote Ecosystem Status

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
| Windows desktop | BUILD/NATIVE PASS; trust boundary | Previously produced x64 PE/NSIS artifacts passed native Windows launch validation; trusted publisher certificate remains external |
| macOS desktop | BUILD/NATIVE PASS; trust boundary | Previously produced arm64 app ZIP/DMG passed native macOS launch validation with ad-hoc signature; Developer ID/notarization remains external |
| Android | BUILD PASS; DEVICE BLOCKED | Owner-sideload APK passed build/lint/package/signature/alignment checks; no physical device or emulator is attached for an install/runtime result |
| iOS | STATIC PASS; EXTERNAL BLOCKED | Expo static export passed; Apple hardware, account, signing, and physical-device validation remain external |
| Notification routing | LOCAL PASS; PROVIDER BLOCKED | Local notification contracts pass; remote push delivery needs an authorized FCM/APNs/EAS provider and registered physical device |
| Public internet exposure | DISABLED | No public Funnel or router forwarding is enabled; owner devices must be enrolled in the private tailnet |

## Phase H validation

- Backend authentication/session/workflow/remote-harness tests: **91 passed**.
- Web session/platform tests: **12 passed**.
- Mobile session, API, deep-link, network, notification, and workflow tests:
  **42 passed**.
- Remote gateway configuration and private-target guards: **passed**.
- Authenticated production remote smoke: **passed**, including exact cleanup.
- Security gate: **passed** with no critical/high findings or secret leak.

The local userspace Tailscale daemon does not install host MagicDNS routing.
The production smoke therefore uses Tailscale's own userspace TCP transport to
the same private Serve endpoint while retaining the real TLS hostname/SNI. An
enrolled remote device uses the normal tailnet path.

## Owner/external actions still required

- Enroll each real phone/desktop in the owner's tailnet and keep the tailnet
  access policy restricted to those identities/devices.
- Install and launch the Android APK on a physical device (or attach an
  emulator) before claiming `DEVICE PASS` for Android.
- Configure an authorized push provider and device registration before
  claiming remote notification delivery.
- Supply trusted Windows and Apple signing identities for public distribution;
  Apple notarization additionally needs an Apple developer account/service.
