# AI OS Phase 12 Device Status

Application source commit: `1145fd36dc7ddc54bf47d0b0edc9787ff2f66357`.
Build-host and emulator evidence is deliberately separated from physical-device
evidence.

| Target | Status | Evidence or exact boundary |
|---|---|---|
| Current Ubuntu 24.04 x86_64 workstation | VERIFIED | Production Tauri binary and AppImage opened real X11 windows and closed cleanly; DEB and AppImage packaging passed |
| Web/PWA | VERIFIED | Production build, manifest/service worker, authenticated Chromium install/navigation/chat/cache isolation/logout E2E passed |
| Android ARM64 physical device | DEVICE_BLOCKED | Fresh ARM64-v8a APK passed package, SDK, signature, alignment, archive, path and secret checks; `adb devices -l` returned no device, so install/launch/voice/push were not claimed |
| Android emulator | VERIFIED (existing evidence) | Phase 11 Android 16/API 36 x86_64 install, launch, process/activity health and Mission Control navigation remain valid; it is not ARM64 physical-device proof |
| Windows physical machine | DEVICE_BLOCKED | Current-commit hosted Windows run `33753597268` passed tests, native x64/NSIS build, scan and process launch. No owner-controlled physical Windows machine was available |
| macOS physical machine | DEVICE_BLOCKED | Current-commit hosted macOS run `33753602442` passed tests, app/DMG build, strict ad-hoc signing, scan and launch. No owner-controlled physical Mac was available |
| iOS physical device | DEVICE_BLOCKED | iOS static Expo export passed; no Apple hardware, provisioning profile, signing identity or physical device was available |
| Remote/private access | VERIFIED (existing evidence) | Phase 11 private Tailscale TLS authentication, desktop/mobile continuation, session revocation/logout and cleanup remain valid; public Funnel stays disabled |
| Physical push receipt | DEVICE_BLOCKED | Local notification routing passed, but no FCM/APNs/EAS credential or registered physical-device token was available |

## Current source artifacts

| Platform | Artifact | SHA-256 | Validation |
|---|---|---|---|
| Ubuntu x86_64 | `/home/md-wasim/AI_Workspace_Data/releases/phase12-1145fd3/Work_Station_Ubuntu.AppImage` | `ad16aa84ecfa140021fa5521afb9f874cbd0a9735652406b698e359aafc18163` | Local build and real X11 launch |
| Ubuntu x86_64 | `/home/md-wasim/AI_Workspace_Data/releases/phase12-1145fd3/Work_Station_Ubuntu.deb` | `43d3783ccccb2ae3f53b38b6a0457140c61d36f424ae6873e9e3e5fe69ab6261` | Package identity/integrity |
| Windows x86_64 | `/home/md-wasim/AI_Workspace_Data/releases/phase12-1145fd3/Work_Station_Windows_Setup.exe` | `825e2c58321182f09541e2079410a9a9e2e0fcb3556b20983cf85ff774b94382` | Hosted native CI build/scan/launch; local PE/NSIS/checksum/archive validation |
| Windows x86_64 | `/home/md-wasim/AI_Workspace_Data/releases/phase12-1145fd3/Work_Station_Windows.exe` | `6a2413b60df5b4f194003dd9ec1cbd5ae5dac1d05221a4fb4832a50f49e42db0` | Hosted native CI build/scan/launch; local PE/checksum validation |
| macOS arm64 | `/home/md-wasim/AI_Workspace_Data/releases/phase12-1145fd3-macos/Work_Station_macOS.app.zip` | `8e2c60fce5aace8be41ddac214d1f68ea5b5160b492649bd67f4528aba0d743b` | Hosted native CI ad-hoc sign/scan/launch; downloaded checksum/archive verified |
| macOS arm64 | `/home/md-wasim/AI_Workspace_Data/releases/phase12-1145fd3-macos/Work_Station_macOS.dmg` | `4f659c1768b28420f80601b337581dc4ab582a7955e955acc8e099a7e981dcb7` | Hosted native CI image integrity/launch; downloaded checksum verified |
| Android ARM64 | `/home/md-wasim/AI_Workspace_Data/releases/phase12-1145fd3/Work_Station_Android.apk` | `c01f781c5eff9d740ece24f47b5b80daeb31a2c6cdbc39c5200bd371ea780be1` | Native release build, arm64-v8a, API 36, debug-certificate owner sideload, no physical launch |
| Web/PWA | `/home/md-wasim/AI_Workspace_Data/releases/phase12-1145fd3-web/Work_Station_Web_PWA.tar.gz` | `30c286280af86207d0a4d5669a6719edfb6c4849e0d0d885d156b3cc1b3c5769` | Production build/PWA E2E, safe-path archive and secret scan |

The consolidated five-artifact manifest is at
`/home/md-wasim/AI_Workspace_Data/releases/phase12-1145fd3/release-manifest.json`
(SHA-256
`d9db2fedc441f5345435de50dd3abb71eaca2611695cd0655ddf302ba58adf94`).

## Distribution boundaries

- Windows artifacts are unsigned and owner-sideload only until a trusted
  publisher certificate is supplied.
- The macOS artifact is ad-hoc signed. Developer ID signing, notarization and
  stapling require the owner's Apple Developer credentials.
- Android uses the standard debug certificate. Physical ARM acceptance and
  public distribution require an owner-controlled ARM64 device and release
  keystore/store account.
- Native iOS packaging requires Apple hardware, developer account,
  provisioning/signing and a physical device.
