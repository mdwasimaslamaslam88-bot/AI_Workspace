# External Boundaries and Documented Gaps

The registry is authoritative: 187 capabilities are implemented, 14 remain
runtime-dependent, 39 remain external-dependent, and 5 remain visibly disabled
planned controls. No item below is reported as locally live without its system.

## Platform and owner credentials

- Windows trusted publisher signing requires an owner-controlled code-signing
  certificate. The current PE/NSIS artifacts are unsigned owner-sideload builds.
- macOS Developer ID trust, notarization and stapling require an Apple Developer
  account and private signing credentials. The current arm64 app is ad-hoc
  signed and strict-valid, but not Apple-trusted.
- Android Play Store publication requires the owner's release keystore and
  store account. The current APK uses the standard debug certificate. An
  Android 16/API 36 x86_64 emulator install/launch passed, but the ARM64 release
  still requires physical-device acceptance.
- Native iOS packaging/device acceptance requires Apple hardware, signing,
  provisioning and a device/account; only the static Expo iOS export was run.

## External capability groups (39 registry entries)

- Realtime communications (7): phone call, configured callback, video,
  screen share, live camera stream, provider-grade interruption and natural
  turn-taking require an authorized WebRTC/telephony provider. Phone and
  callback requests now have owner-scoped desktop/web and Android activation
  paths through the encrypted connector gateway, but no carrier connector is
  configured in production and no external call is claimed.
- Extended speech profiles (3): verified female and multilingual/mixed-language
  models must be installed and admitted before these profiles can be enabled.
- Connected productivity (18): email, calendar, meetings, CRM, social,
  publishing/analytics/leads, webhooks, REST, GraphQL, OAuth, SDK, database,
  local API, browser and desktop automation require provider authorization and
  owner consent. The generic encrypted loopback connector E2E passed; that does
  not promote provider-specific integrations.
- Broker execution (4): broker integration, live orders, risk confirmation and
  permission enforcement require a legitimate broker account/API and owner risk
  policy. Research, backtesting and paper trading passed locally; no live trade
  was claimed.
- Protected experiences (4): adult gating, consent, minor safety and
  jurisdiction controls require lawful age/jurisdiction/consent verification;
  they remain disabled. Illegal, exploitative, non-consensual, or minor sexual
  content is never enabled.
- Video creation (2): video and multimedia creation require a verified admitted
  runtime or legitimate provider.
- Pronunciation scoring (1): acoustic pronunciation scoring requires a verified
  provider/runtime; local curriculum and speaking practice do not claim it.

Exact IDs, UI states and dependency lists are in
`reports/feature-gap-list.json`.

## Runtime-dependent capabilities (14)

Microphone/STT/TTS/playback, language detection, camera/vision, file/document
grounding, image generation/editing, and the image/audio/voice workspaces remain
gated on installed, integrity-verified local models and private asset storage.
They passed on the current configured workstation where reported, but the
registry correctly recomputes readiness on another machine.

## Planned controls (5)

`mission_approve`, `mission_manual_retry`, `mission_modify`, `mission_pause`,
and `mission_resume` remain disabled/documented until the persistent mission
scheduler contract exists. Existing mission creation, streaming, cancellation,
bounded automatic retry, deadline, verification and results are implemented;
the five controls are not mislabeled as ready.

## AI quality boundary

The canonical score is 97.88/100, not 100. The two remaining failures require a
stronger stable hardware-safe coder model/profile that preserves the complete
coding and model-comparison categories. No installed candidate met that gate.
