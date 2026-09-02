# Platform Status

| Platform | Build | Runtime/launch evidence | Distribution trust | Status |
| --- | --- | --- | --- | --- |
| Ubuntu Desktop | x86-64 Tauri AppImage and DEB | Production binary and AppImage opened real windows and closed cleanly on Ubuntu | Owner-sideload | PASS |
| Windows Desktop | x64 portable PE and NSIS installer | Native Windows run `33688155298` validated PE metadata, extracted/tested NSIS, scanned artifacts, and kept the app alive for the launch smoke | Unsigned; trusted publisher certificate is external | PASS with signing boundary |
| macOS Desktop | arm64 `.app` ZIP and DMG | Native macOS run `33691644096` verified DMG checksum, bundle metadata, strict codesign resource envelope, scans, and a 12-second process launch | Valid ad-hoc signature; Developer ID/notarization is external | PASS with trust boundary |
| Android | Optimized owner-sideload APK, package `com.workstation.personalai`, min SDK 24, target SDK 36 | Native build/lint/package identity, v2/v3 signing, alignment and artifact-safety checks passed | Standard Android debug certificate; no device/emulator was attached; Play signing is external | BUILD PASS; device/store boundary |
| Web/PWA | Production Vite bundle and deterministic archive | Chromium install/navigation/auth/chat/cache-isolation/logout E2E passed; service worker and manifest validated | Self-hosted private deployment | PASS |
| iOS companion source | Expo static iOS export | Static export passed; no native `.ipa` or physical-device run was performed | Apple hardware/account/signing required | Static PASS; native external boundary |

The web entry is 485,078 bytes before compression and is guarded below 500 KiB.
Eleven feature workspaces are emitted as on-demand chunks. Desktop, mobile and
web use the same authenticated backend contracts and feature authority.

Artifact paths and hashes are listed in `RELEASE_REPORT.md` and the external
release directory's `SHA256SUMS`.
