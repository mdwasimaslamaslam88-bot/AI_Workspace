# Grounded finance intelligence

AI OS provides an authenticated, owner-scoped finance workspace at
`/api/v1/finance/workspaces`. Paper remains the unconditional default. A
separate broker gateway now exists, but it cannot execute unless an owner has
configured and health-verified exact market-data and broker connectors,
persisted a bounded risk policy, and explicitly enabled live trading.

## Implemented workflows

Each owner can create up to ten finance workspaces with an ISO base currency,
paper cash, and explicit maximum-order and maximum-position policies. The
implemented workspace supports Indian stocks, global stocks, crypto, and FX
symbols through:

- source-grounded local-model research and paper-strategy artifacts;
- deterministic moving-average backtesting over supplied, ordered price bars,
  including fees, deterministic slippage, position sizing, optional stop-loss
  and take-profit rules, return, CAGR (when numerically defined), Sharpe-like
  return, win rate, exposure and maximum drawdown;
- watchlists and source-evaluated threshold alerts;
- explicitly confirmed paper buys and sells with order, position, cash, and
  owner-confirmation checks;
- deterministic portfolio valuation and concentration-risk analysis from an
  exact quote set; and
- a source-linked paper-trading journal.

The service stores prices as bounded integer minor units and quantities as
bounded micro-units. Historical bars and quotes require timezone-aware
observation times and nonblank source references. AI OS does not invent a
market feed. Research receives only owner-supplied facts, labels them as
untrusted data rather than instructions, and prohibits prediction or profit
guarantees. The local model receives only `model_inference`; it receives no
broker, network, filesystem, or tool permission. Its untouched output is
persisted only after the Agent OS verifier records a matching SHA-256 and model
identity. A finance-specific objective verifier independently checks that the
untouched output cites every exact source reference, contains the required
research uncertainty/counter-evidence or paper-strategy entry/exit/risk/
invalidation contract, and makes no explicit guaranteed-profit claim.

## Normalized market data

`POST /api/v1/finance/market-data/quotes/resolve` uses an owner-scoped
connector with the `market.quote.read` capability. The provider payload must
identify the provider, asset class, symbol, exchange, currency, timestamp,
timezone and last price. Bid/ask, OHLC and volume are normalized when supplied.
Provider identity mismatches, malformed ranges, future observations and stale
quotes fail closed. Accepted quotes carry their connector execution audit ID as
the source reference. Indian stocks, global stocks, crypto and FX share this
one internal contract; no provider-specific assumptions enter portfolio or
trading policy logic.

## Backtesting and risk

The backtester uses one fixed, versioned moving-average simulation. It validates
strictly ordered bars and bounded assumptions, emits canonical JSON and never
describes historical output as future performance. Paper market orders are
immediate full-fill-or-reject simulations; because the paper contract has no
resting orders, its partial-fill and cancellation states are intentionally not
claimed. The separate broker evidence contract preserves a provider-reported
partial fill as `verified_open` and recognizes a separately verified cancelled
state, without attributing either behavior to the paper simulator.

Paper orders never call a connector. An order is recorded as rejected unless
the owner confirms it and it passes the configured order, position, cash, or
holding checks. The web and mobile labels use explicit paper language. Portfolio
valuation requires one matching quote for every open position; missing,
duplicate, or extra quote identities are rejected. Alert evaluation persists
the exact supplied price, source, and observation time.

## Broker safety wall

The owner-facing Finance panel and API expose policy configuration, explicit
live enable/disable, an emergency kill switch and a secret-free audit view.
Policy configuration first reads and validates a provider account response,
including account identity, live permission, MFA/session validity, currency,
positions, exposure, daily P&L, open orders and market-session state. It stores
only a SHA-256 of the account reference.

Every broker submission is serialized behind the persisted policy and checks
execution mode, owner authorization, connector health/revocation, exact
origin/path/scope/capability, account identity, session validity, risk limits,
allowed instruments and venues, market hours, quote freshness, kill switch,
and a bounded idempotency key. The generic connector endpoint cannot issue
broker writes. HTTP success is insufficient: the gateway requires a matching
provider acknowledgement and then a separate order-status readback. Stored
evidence contains hashes and connector audit IDs, not credentials or raw broker
account/order identifiers. Activating the kill switch blocks new live orders
before any network call and preserves positions and audit history.

## Isolation and external boundaries

Composite database foreign keys bind workspaces, watch items, positions,
orders, alerts, and artifacts to one owner. Repository queries are owner scoped,
foreign workspace identities return the same not-found response, response
contracts reject any backend claim of live execution, and source text is not
logged.

Actual feeds and live broker execution remain registered `external_dependency`
features. The local gateway is `RUNTIME_READY`; `EXTERNAL_LIVE` additionally
requires legitimate owner credentials, licensed provider access, exact egress
origins, broker approval/KYC, OAuth or API consent, MFA/OTP/session validity,
account permissions and provider-side evidence. None is inferred or bypassed.

## Verification

Automated evidence covers normalized quote validation/freshness, deterministic
agent math, paper isolation, every live-risk boundary, kill-switch pre-network
denial, generic-route bypass denial, idempotent order intent, provider
acknowledgement/status verification, partial/full fill evidence, policy audit
hashes, owner isolation, concurrent duplicate submission, web/mobile contracts
and exact Alembic/ORM parity. Disposable PostgreSQL tests execute an allowlisted
loopback provider workflow; that is local protocol evidence and is never labeled
external-live.
