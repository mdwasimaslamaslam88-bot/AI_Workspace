# Phase 11 Platform Status

| Platform | Build | Runtime/launch evidence | Distribution trust | Status |
|---|---|---|---|---|
| Ubuntu Desktop | x86-64 Tauri AppImage and DEB | Current production binary and AppImage opened real X11 windows and closed cleanly | Owner-sideload | DEVICE PASS |
| Windows Desktop | x64 standalone PE and NSIS installer | Native run `33735245448` built exact commit `a82b5b5`, inspected/extracted NSIS, scanned artifacts and kept the app process alive | Unsigned; trusted publisher certificate is external | NATIVE PASS with signing boundary |
| macOS Desktop | arm64 `.app` ZIP and DMG | Native run `33735249778` built exact commit `a82b5b5`, verified bundle/DMG, strict ad-hoc signature, scans and process launch | Developer ID/notarization/stapling require owner Apple credentials | NATIVE PASS with trust boundary |
| Android | ARM64 owner-sideload APK plus x86_64 test build | Android 16/API 36 emulator installed/launched `com.workstation.personalai`; `MainActivity` stayed top-resumed and bottom navigation opened Mission Control | Standard debug certificate; physical ARM acceptance and owner store signing remain external | EMULATOR DEVICE PASS; ARM/store boundary |
| Web/PWA | Production Vite bundle and deterministic archive | Chromium install/navigation/auth/chat/cache-isolation/logout E2E, service worker and manifest passed | Private authenticated self-hosting | RUNTIME PASS |
| iOS companion source | Expo static iOS export | Static export passed; no native `.ipa` or physical-device run | Apple hardware, account, provisioning, signing and physical device required | STATIC PASS; native external boundary |

The current web entry is 489,282 bytes before compression and remains below its
500 KiB release guard. Eleven feature workspaces load on demand. Desktop,
mobile and web share the authenticated backend contracts, missions, memory,
permissions and feature authority.

## Android device detail

The available AVD was x86_64 and the host exposed no `/dev/kvm`. The first
normal start correctly refused unaccelerated x86 execution. A bounded retry
with the emulator's explicit `-accel off` mode booted Android 16 in 182.289
seconds. A transient Android `system` ANR appeared under software emulation;
choosing `Wait` recovered it. The WORK STATION process remained live, produced
no application fatal exception, exposed its accessibility hierarchy, and
navigated from AI Presence to Mission Control. The emulator was shut down and
left no attached device process.

## Artifact authority

Release root:
`/home/md-wasim/AI_Workspace_Data/releases/phase11-a82b5b5`

The eight production artifacts and SHA-256 values are listed in
`reports/RELEASE_REPORT.md` and the release root's `checksums.sha256`. Archive
integrity, Windows PE/NSIS identity, macOS artifact manifests, Linux package
structure, Android signature/alignment/ABI, PWA contents, extracted private
path markers and secret material were independently rechecked.
