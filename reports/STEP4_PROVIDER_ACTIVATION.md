# Step 4 — Market data and trading activation

Status: **PARTIAL**. All implemented local work is passing. No owner market-data
or broker provider is configured, so no external feed, broker sandbox, account,
order or fill is reported live.

## Capability results

| Capability | Local state | External state | Objective evidence |
|---|---|---|---|
| Market data | RUNTIME_READY | EXTERNAL_BLOCKED | One normalized contract covers Indian equities, global equities, crypto and FX. It rejects stale/future timestamps, malformed OHLC/spreads and provider identity mismatches and binds accepted data to a connector audit ID. |
| Research/analytics | LOCAL_PASS | EXTERNAL_BLOCKED | Source-isolated research, watchlists, alerts, portfolio valuation, P&L, allocation/concentration and risk outputs are verified from supplied data. Licensed external feeds remain absent. |
| Backtesting | LOCAL_PASS | EXTERNAL_BLOCKED | Deterministic v2 engine verifies fees, slippage, position sizing, optional stop/take exits, trade log, return, CAGR when defined, Sharpe-like return, win rate, exposure and maximum drawdown. External historical data remains provider-dependent. |
| Paper trading | LOCAL_PASS | EXTERNAL_BLOCKED | Isolated paper mode remains the default and cannot route to a connector. Paper orders are immediate full-fill-or-reject; resting/partial/cancel paper behavior is not claimed. |
| Broker gateway | RUNTIME_READY | EXTERNAL_BLOCKED | Disposable PostgreSQL + exact-origin HTTP transport completed account preflight, fresh quote, order submission, acknowledgement, independent status readback and secret-free persistence. Concurrent identical submissions produced exactly one provider request and one broker record. This is local protocol evidence, not a third-party provider. |
| Live safety | LOCAL_PASS | OWNER_ACTION | Persisted owner policy, explicit enable/disable, account/MFA-session recheck, risk limits, allowed symbols/venues, market hours, quote freshness, kill switch, idempotency and audit enforcement pass. |

## Security result

- Production connector runtime is unconfigured and approved external origins
  are `[]`; egress therefore remains deny-all.
- Generic connector execution cannot use broker mutation capabilities. Only the
  dedicated finance gateway can assert them after policy validation.
- Revoked, disabled, unhealthy, wrong-owner, wrong-origin, wrong-path and
  missing-capability connectors fail closed through the connector layer.
- The broker account and provider order references are stored only as SHA-256;
  credential material is not returned by these APIs or written to finance
  audits.
- A provider HTTP 2xx is not success evidence. Matching acknowledgement and
  separate status verification are mandatory. Full, partial/open and cancelled
  provider states are stored only after that readback succeeds.
- Activating the kill switch disables live authorization and blocks a new order
  before any network request. Re-enabling requires exact owner confirmation and
  a fresh account/session check.

## Production inventory

Approved external origins: none. Configured, enabled, healthy, credentialed,
sandbox and verified external-live finance connectors: zero. Real-money orders
placed: zero.

## Exact owner actions

1. Choose legitimate licensed providers for the required asset classes and a
   broker sandbox first.
2. Add only their exact origins and API path prefixes to operator egress policy.
3. Complete provider authentication/consent and broker account approval,
   KYC/MFA/OTP/session requirements using the Provider Center.
4. Discover and approve minimum capabilities: `market.quote.read`,
   `broker.account.read`, `broker.order.submit`, and `broker.order.status`.
5. Enter bounded risk limits and allowlists, keep the kill switch active, and
   run provider-side read and sandbox tests.
6. Live trading remains disabled until the owner separately enters the exact
   enable confirmation. This Step 4 run grants no real-money authorization.

Machine-readable evidence is in `reports/STEP4_PROVIDER_EVIDENCE.json`.

## Verification result

- Focused finance/backend safety suite: **43 passed**.
- Focused finance web suite: **7 passed**.
- Focused mobile finance contract suite: **2 passed**.
- Full backend regression: **2,993 passed**, **51 intentional skips**.
- Full web regression: **200 passed**; typecheck, lint and production build
  passed.
- Full mobile regression: **64 passed**; shared/mobile typecheck, lint, static
  exports and Expo Doctor **21/21** passed.
- PostgreSQL integration: **51 passed**; migrations through
  `0020_trading_safety`; Alembic drift check passed.
- Native Android debug packaging and identity/signing/alignment checks passed
  using the installed SDK.
- Desktop Rust tests, production binary, AppImage/DEB packaging and production/
  AppImage launch smoke passed.
- Browser/PWA E2E and the complete real local runtime matrix passed. The finance
  runtime verified research, deterministic backtesting, isolated paper orders,
  portfolio/risk, alerts and journal; it explicitly reported the live broker
  boundary as an external dependency.
- Security/release gates passed with **0 critical**, **0 high** and **14 known
  moderate** transitive Expo build-tool advisories. No forced breaking upgrade
  was applied.
