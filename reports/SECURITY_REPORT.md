# Current Final Security Report

## Mandatory gate: PASS

The production and release gates passed:

- tracked credential/private-key signature scanning;
- compiled web/mobile operator-token, credential and host-path scanning;
- provider secrets kept write-only, encrypted and outside model prompts,
  responses, reports and logs;
- connector origin/path/scope allowlists, redirect denial, bounded payloads,
  rate limits, retry/circuit-breaker behavior, audit linkage and revocation;
- authenticated owner isolation for conversations, memory, RAG, missions,
  sessions, connectors, marketing, finance, learning, creative assets, tools
  and workflows;
- denial of unregistered shell, code and unrestricted-network tools;
- desktop CSP and Android cleartext-network policy checks;
- PostgreSQL constraints/migrations through `0020_trading_safety` with
  no schema drift;
- private loopback backend and authenticated private Tailscale TLS gateway;
- public Tailscale Funnel and unrestricted remote target guards;
- systemd unit syntax and user-manager-compatible sandboxing;
- backup checksum/archive/member/permission verification and disposable restore;
- stale self-update candidate fail-closed reconciliation and rollback tests;
- Windows/macOS native CI secret/path scans and launch checks;
- APK package/ABI/signature/alignment checks; and
- consolidated release hash, archive, extracted-path and secret scans.

Production egress remains deny-all until an owner registers an exact legitimate
provider origin and scopes. External connector count is zero. Local API
documentation is disabled in the active remote production mode.

Step 4 additionally verified that generic connector paths cannot issue broker
mutations, paper mode cannot route to a provider, every live order requires a
persisted owner risk policy and explicit live state, and the emergency kill
switch blocks before network access. Order acknowledgement and independent
status/fill readback are linked to concrete connector execution audits;
concurrent duplicate submissions produce one provider request. The local
protocol checks used exact-origin loopback endpoints only. No live provider,
account balance, order or fill is claimed, and no real-money order was attempted.

## Dependency findings

- Critical findings: **0**
- High findings: **0**
- Moderate findings: **14**

The moderate findings are transitive Expo build-tool advisories, including
`decode-uri-component` and `uuid`. Automated forced fixes would replace the
current Expo stack with breaking/incompatible versions, so they were not
applied without compatibility evidence. This residual build-tool risk is
recorded rather than misreported as zero.

## Artifact scan classification

All eight production artifacts passed their provider/native manifests and the
consolidated SHA-256 manifest. Expanded unpacked scanning found no provisioning
marker, private workstation path or project key. The AppImage legitimately
bundles Ubuntu's `libgnutls.so.30`, which contains crypto parser/self-test PEM
fixtures. The six fixture length/SHA-256 pairs exactly matched the installed
Ubuntu distribution library. The validator treated only that exact proven
system-library set as non-secret; a PEM key block anywhere else remained a
release failure.

## Artifact trust boundaries

- Linux and Web/PWA: private owner/self-hosted artifacts with recorded hashes.
- Windows: native build/scan/launch validated but unsigned; owner-sideload only
  until a trusted publisher certificate is supplied.
- macOS: strict-valid ad-hoc signature for the native artifact; Developer ID,
  notarization and stapling require owner Apple credentials.
- Android: v2/v3-signed with the standard Android debug certificate; the
  x86_64 emulator package passed device launch, while physical ARM acceptance
  and Play release signing remain external.
- iOS: no native artifact claim; Apple hardware, account, provisioning, signing
  and device testing are external boundaries.

No claim of being unhackable is made. The result is defense-in-depth with the
explicit residual and external boundaries above.
