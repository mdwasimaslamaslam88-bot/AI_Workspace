# Mobile application

`apps/mobile` is an Expo SDK 57 application for Android and iOS. It imports the
same shared contracts as the web application and delegates all Personal AI
logic to the backend.

Implemented client contracts include secure session restore/logout, connection
recovery on network and app resume, conversation creation and text generation,
keyset-paginated conversation and message history, bounded owner-wide title/message search,
owner-scoped pin/archive/restore/rename, immutable duplicate/edit-resend/regenerate
branches, confirmed deletion, native Markdown/code presentation, explicit
clipboard copy, owner-scoped RAG source labels/excerpts, owned file/image/camera
uploads, microphone recording and speech recognition, and on-demand authenticated
image and audio playback directly in chat. Model-provided links and HTML remain
inert text, so rendering cannot initiate navigation or network access. Generic
local notification architecture, deep links, and safe error redaction are also
included. SecureStore maps to
Keychain/Keystore-backed device storage. The UI follows the device light/dark
preference, and the private settings view includes redacted runtime diagnostics
plus an authoritative hardware-aware model catalog, per-device session naming,
issuance, revocation, logout, and bearer rotation. Newly issued credentials
remain transient until the owner saves them on the intended device. The owner
settings screen blocks capture while mounted, uses a protected app-switcher
preview, and clears the one-time credential whenever the app leaves the
foreground.

All native press targets declare an explicit accessibility role, selection and
checked state are announced where applicable, destructive actions have clear
labels, and compact chat/Studio controls retain at least a 44-point touch
target. A static TypeScript-AST regression test prevents unlabeled Pressable
controls from entering the mobile client.

Private message media is fetched only after an owner action and removed from
the app cache when its component unmounts. Image and document rows do not
initialize native audio players; audio resources are allocated only for audio
attachments.

The Studio tab uses the existing owner-scoped backend APIs for explicit memory,
allowlisted tool execution, bounded workflow creation/start/cancel/status,
image generation/editing, and text-to-speech. It does not duplicate AI or
authorization logic. Generated media is downloaded with the bearer credential
in the request header, written only to the app cache for display/playback, and
removed when the Studio screen closes. Credentials are never placed in media
URLs, filenames, notifications, or source. All upload and generation
idempotency headers are backend-compatible UUIDs.

Workflow notifications are emitted only after authenticated polling observes a
server-authoritative terminal status. Polling is bounded beyond the backend's
60-second workflow deadline, resumes for running work when the Studio reloads,
and stops without inventing a result when the owner cancels or leaves the
screen. Failed, timed-out, or unexpectedly interrupted monitoring uses the same
generic, content-free attention notification.

Chat media uses the same authenticated download route. The bearer stays in the
request header, never enters a media URL, and image/audio bytes are written only
to a randomized app-cache file after the owner explicitly chooses to load them.
The cache entry is removed when the rendered attachment leaves the screen.

## Local development

The default loopback URL works only when the runtime shares the workstation's
network namespace. A physical phone must use the private HTTPS Tailscale
MagicDNS endpoint; non-loopback HTTP is rejected.

```bash
EXPO_PUBLIC_API_BASE_URL=https://workstation.example.ts.net \
  npm run start --workspace @work-station/mobile
```

## Validation

```bash
npm run test --workspace @work-station/mobile
npm run typecheck --workspace @work-station/mobile
npm run lint --workspace @work-station/mobile
npm run build:static --workspace @work-station/mobile
npx expo-doctor
./scripts/mobile_check.sh
```

The static build command produces independent Android and iOS JavaScript
bundles on any supported development host. It does not claim a signed native
device package; those target-host/account requirements remain below. The
offline typecheck/config validator also requires exactly one installed and
locked React runtime matching Expo's compatibility map. Expo Doctor adds its
online application-schema check when the Expo API is reachable.

When an Android SDK is available, use the repeatable native gate from the
repository root:

```bash
WORK_STATION_ANDROID_SDK_ROOT=/absolute/path/to/android-sdk \
  ./scripts/mobile_check.sh --require-native-android
```

The gate performs a fresh managed prebuild in a randomly named temporary
directory, compiles an x86_64 debug APK by default, verifies the application
identifier and scans the unpacked artifact for operator credential material,
then removes the temporary project. It compares Git state before and after so
native validation cannot silently rewrite the managed Expo source. Set
`WORK_STATION_ANDROID_ARCHITECTURES` only when another locally installed ABI is
needed. This is a native compilation/configuration gate, not a signed release
artifact and not a substitute for the owner-controlled EAS credentials below.

To retain an actual ARM64 package for private owner sideloading, provide a new
absolute output path:

```bash
WORK_STATION_ANDROID_SDK_ROOT=/absolute/path/to/android-sdk \
  ./scripts/mobile_check.sh \
  --output-apk /absolute/output/Work_Station_Android.apk
```

This builds the optimized release variant, runs Android release lint, verifies
package name/version/label, ZIP integrity, alignment, and signature, removes
native compiler-root strings without changing ELF offsets, re-aligns and
re-signs the package, and scans its unpacked content for operator material,
private keys, and build-machine paths. The APK uses Android's standard debug
certificate and is suitable only for owner sideloading; it is not a Play Store
release. No backend credential is embedded.

## Release builds

`apps/mobile/eas.json` defines development, preview, and production profiles.
Android application ID and iOS bundle ID are both
`com.workstation.personalai`; the deep-link scheme is `work-station`.
Incoming links are reduced to fixed owner-safe routes only: chat, settings, or
the private Studio umbrella. Query parameters, fragments, credentials, nested
paths, tokens, and object identifiers are rejected and fall back to Chats.

Store-distributed Android packages require owner-controlled release signing;
iOS device/TestFlight packages require an Expo/EAS or native signing setup plus
owner-controlled Apple Developer credentials. iOS simulator/signing also requires
macOS/Xcode. Those external accounts are intentionally absent from source and
cannot be fabricated. Follow the current Expo build documentation at
<https://docs.expo.dev/build/introduction/>.

Remote push delivery is an architecture boundary, not silently enabled: it
requires owner-approved APNs/FCM/EAS credentials and a private backend device
registration/revocation design. Current notifications are local and generic,
with no sensitive message preview. Android Expo Go does not ship the native
notification module; WORK STATION detects Expo Go before importing it, keeps
chat, Settings, and Studio usable without a development error overlay, and
treats notification permission/alerts as unavailable. Native development and
production builds retain the configured notification integration.
