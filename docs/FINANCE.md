# Grounded finance intelligence

AI OS provides an authenticated, owner-scoped finance workspace at
`/api/v1/finance/workspaces`. It is an analysis and paper-simulation system,
not a broker. The API, web/desktop Finance panel, and mobile Studio all report
`execution_mode: paper` and `live_broker_status: external_dependency`.

## Implemented workflows

Each owner can create up to ten finance workspaces with an ISO base currency,
paper cash, and explicit maximum-order and maximum-position policies. The
implemented workspace supports Indian stocks, global stocks, crypto, and FX
symbols through:

- source-grounded local-model research and paper-strategy artifacts;
- deterministic moving-average backtesting over supplied, ordered price bars;
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

## Backtesting and risk

The backtester uses one fixed, versioned moving-average simulation. It validates
strictly ordered bars, window bounds, fee bounds, and starting cash, then emits
canonical JSON with trades, ending equity, return basis points, maximum
drawdown, open-position state, the supplied source, and
`profit_guarantee: false`. It does not fit or tune parameters and does not
describe historical output as future performance.

Paper orders never call a connector. An order is recorded as rejected unless
the owner confirms it and it passes the configured order, position, cash, or
holding checks. The web and mobile labels use explicit paper language. Portfolio
valuation requires one matching quote for every open position; missing,
duplicate, or extra quote identities are rejected. Alert evaluation persists
the exact supplied price, source, and observation time.

## Isolation and external boundaries

Composite database foreign keys bind workspaces, watch items, positions,
orders, alerts, and artifacts to one owner. Repository queries are owner scoped,
foreign workspace identities return the same not-found response, response
contracts reject any backend claim of live execution, and source text is not
logged.

Live broker connectivity and live order execution remain registered
`external_dependency` features. Enabling them would require a legitimate
broker account and API, owner authorization, broker scopes and rules, a
separate connector implementation, configurable risk confirmation, and
end-to-end verification. AI OS does not bypass authentication, MFA, OTP,
billing, suitability checks, market rules, or owner confirmation, and it makes
no claim or guarantee of profit.

## Verification

Automated evidence covers deterministic agent math, schema bounds, paper-only
API contracts, client response validation, web/mobile paths, exact Alembic/ORM
parity, owner isolation, cross-owner foreign-key rejection, source persistence,
alert transitions, confirmation and risk rejections, and real local-model
research and paper-strategy smokes against a disposable PostgreSQL database.
The runtime smoke also executes the backtest, paper order, valuation, risk,
alert, and journal paths without a broker connection.
