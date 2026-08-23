# Mobile application

`apps/mobile` is an Expo SDK 57 application for Android and iOS. It imports the
same shared contracts as the web application and delegates all Personal AI
logic to the backend.

Implemented client contracts include secure session restore/logout, connection
recovery on network and app resume, conversation creation and text generation,
loaded-chat search, owner-scoped pin/archive/restore/rename, immutable
duplicate/edit-resend/regenerate branches, confirmed deletion, owned
file/image/camera uploads, microphone recording and speech recognition,
authenticated image and audio playback, generic local notification architecture,
deep links, and safe error redaction. SecureStore maps to
Keychain/Keystore-backed device storage. The UI follows the device light/dark
preference, and the private settings view includes redacted runtime diagnostics
plus per-device session naming, issuance, revocation, logout, and bearer
rotation. Newly issued credentials remain transient until the owner saves them
on the intended device.

The Studio tab uses the existing owner-scoped backend APIs for explicit memory,
allowlisted tool execution, bounded workflow creation/start/cancel/status,
image generation/editing, and text-to-speech. It does not duplicate AI or
authorization logic. Generated media is downloaded with the bearer credential
in the request header, written only to the app cache for display/playback, and
removed when the Studio screen closes. Credentials are never placed in media
URLs, filenames, notifications, or source. All upload and generation
idempotency headers are backend-compatible UUIDs.

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
```

The static build command produces independent Android and iOS JavaScript
bundles on any supported development host. It does not claim a signed native
device package; those target-host/account requirements remain below.

## Release builds

`apps/mobile/eas.json` defines development, preview, and production profiles.
Android application ID and iOS bundle ID are both
`com.workstation.personalai`; the deep-link scheme is `work-station`.
Incoming links are reduced to fixed owner-safe routes only: chat, settings, or
the private Studio umbrella. Query parameters, fragments, credentials, nested
paths, tokens, and object identifiers are rejected and fall back to Chats.

Native signed packages require an Expo/EAS account plus owner-controlled Google
Play and Apple Developer credentials. iOS simulator/signing also requires
macOS/Xcode. Those external accounts are intentionally absent from source and
cannot be fabricated. Follow the current Expo build documentation at
<https://docs.expo.dev/build/introduction/>.

Remote push delivery is an architecture boundary, not silently enabled: it
requires owner-approved APNs/FCM/EAS credentials and a private backend device
registration/revocation design. Current notifications are local and generic,
with no sensitive message preview.
