# Final Security Report

## Mandatory gate: PASS

The final release gate passed:

- tracked credential/private-key signature scan;
- compiled web/mobile operator-token, private-key and host-path scan;
- pip dependency consistency;
- npm high/critical vulnerability gate;
- desktop CSP check against unrestricted HTTPS egress;
- authenticated owner isolation for conversations, memory, RAG, missions,
  connectors, marketing, finance, learning, creative assets, tools and workflows;
- connector credential encryption, scope/path allowlists, rate/retry boundaries,
  audit linkage and revocation;
- denial of unregistered shell/code/unrestricted-network tools;
- private loopback service and remote gateway/Funnel guards;
- database constraints and migrations with zero schema drift;
- Windows and macOS secret/private-key/build-path artifact scans;
- APK package identity, signature-scheme and alignment checks; and
- Git generated-artifact and unexpected-file scan.

Provider keys remain write-only encrypted values outside prompts, responses and
logs. The connector vault uses a separate owner-only key. External actions
remain disabled until an owner authorizes a legitimate provider and scopes.

## Dependency findings

No high or critical npm finding is open. `npm audit` reports 14 moderate
transitive findings in Expo build tooling, including `decode-uri-component`
and `uuid`. The offered forced remediations replace the current Expo stack with
breaking/incompatible versions, so they were not applied without compatibility
evidence. These are recorded residual build-tool risks, not hidden as a clean
zero-finding audit.

## Artifact trust

- Linux and Web/PWA: owner-self-hosted artifacts with recorded hashes.
- Windows: native validated but unsigned; owner-sideload only until a trusted
  publisher certificate is supplied.
- macOS: strict-valid ad-hoc signature (`Identifier=com.workstation.personalai`,
  arm64); not Developer ID signed or notarized.
- Android: v2/v3-signed with the standard Android debug certificate; not a
  Play Store release signature.

No claim of being unhackable is made. The release uses defense in depth and
retains the exact trust boundaries above.
