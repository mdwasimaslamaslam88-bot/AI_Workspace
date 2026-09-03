# Finance and trading status

Evidence refreshed 2026-09-04. AI OS is verified for grounded research,
analytics, deterministic backtesting and paper market simulation. The external
market-data and broker gateway is implemented and locally protocol-verified,
but no third-party provider is configured or reported live.

| Capability | State | Evidence or boundary |
|---|---|---|
| Indian/global stock, crypto and FX normalization | RUNTIME_READY | Provider identity, instrument, venue, currency, timezone, freshness, bid/ask, OHLC and volume contract tested |
| External quotes/candles/fundamentals/news | EXTERNAL_BLOCKED | No licensed owner provider, credential or allowlisted origin is configured |
| Grounded research and strategy | LOCAL_PASS / RUNTIME_READY | Only supplied source facts are accepted; verifier digest, citations, uncertainty and no-profit-guarantee rules remain enforced |
| Portfolio and risk | LOCAL_PASS | Exact quote coverage, valuation, P&L, allocation/concentration and breach evidence are deterministic |
| Backtesting | LOCAL_PASS | Versioned v2 engine covers fees, slippage, sizing, stops/take-profit, trade log, returns, CAGR, Sharpe-like metric, win rate, exposure and drawdown |
| Paper trading | LOCAL_PASS | PAPER is the unconditional default; owner-confirmed immediate market simulations enforce cash/order/position limits and never call a connector |
| Broker connector gateway | RUNTIME_READY | Local exact-origin HTTP + PostgreSQL workflow verified account, fresh quote, submission, acknowledgement, status and audit persistence |
| Broker sandbox/live account | EXTERNAL_BLOCKED | No owner broker account, API grant, KYC, MFA/OTP/session, scopes or provider receipt is available |
| Live-order safety | LOCAL_PASS | Default paper, explicit authorization, persisted limits/allowlists, health, session, market hours, freshness, kill switch, idempotency and alternate-route denial tested |
| Web/desktop owner controls | LOCAL_PASS | Mode, account state, exact-confirmation controls, emergency kill switch and audit counts are exposed |
| Mobile/shared contracts | LOCAL_PASS | Typed market/broker policy, quote, audit and order APIs compile; mobile continues to present paper as default |

No real-money order was attempted. Production connector runtime is unconfigured,
the external-origin allowlist is empty, and verified external-live finance
providers total zero. Exact activation requirements and evidence are recorded in
`reports/STEP4_PROVIDER_ACTIVATION.md` and
`reports/STEP4_PROVIDER_EVIDENCE.json`.
