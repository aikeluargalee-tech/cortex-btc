# CORTEX-BTC

Bidirectional BTC/USDT spot portfolio decision system — **decision support only**.

CORTEX does not trade. It does not hold exchange keys. It produces exactly one
verdict per market snapshot — `BUY`, `HOLD`, `SELL` or `ABSTAIN` — and explains
every gate that led there. You remain the executor.

> **Strategy status: UNVALIDATED.** No claim of profitability or edge is made or
> implied. Every number in the shipped profile is an illustrative research
> starting point, not an optimised parameter.

## Why this repository exists separately

`btc-intelligence-v4` is governed by a constitution that forbids automatic
buy/sell recommendations and "deterministic trading verdicts". CORTEX is built
around exactly such a verdict, so it lives apart. It reuses V4's *patterns* —
quality statuses, freshness policy, configuration versioning, fail-closed gates
— without touching V4's governance or data packet.

## Quick start

Requires Python 3.11+. No third-party runtime packages, no Node, no build step.

```powershell
cd cortex-btc
python -m cortex.server
# http://127.0.0.1:8787
```

Database and configuration default to `data/cortex.db` and
`config/research-seed.json`; override with `CORTEX_DB`, `CORTEX_SEED_CONFIG`
and `CORTEX_PORT`.

The seed profile is deliberately labelled `UNVALIDATED`. It is replaced by a
versioned configuration the moment you save a change in the Configuration tab.

## What is implemented

| Component | State |
|---|---|
| Dual-inventory ledger, event sourced, reservations, fees, cost basis | done |
| Data ingestion from Binance public endpoints, provenance per feature | done |
| Quality gate: six statuses, freshness, fail-closed | done |
| Module A — accumulation, 7 conditions, sequence-based state | done |
| Module B — distribution, 6 conditions, three selling mechanisms | done |
| Module C — sizing as a minimum over named constraints, binding constraint exposed | done |
| Module D — ten gates, one verdict, deterministic, replay key | done |
| Order lifecycle with human-only transitions and reservation rules | done |
| Configuration versioning with before/after/operator/reason | done |
| Command Centre (single screen) with Portfolio/Orders/Config/Audit drawers | done |
| Telemetry charts, backtesting, replay, paper-trading reports | not yet — phases 5–6 |

## Design invariants

1. **Money is `Decimal`.** No floats anywhere in the ledger or sizing path.
2. **The verdict is a pure function.** `decide(market, portfolio, config, previous_state)`.
   No clock, network or randomness inside; timestamps are inputs.
3. **Snapshots are content addressed.** `replay_key` is a hash of market snapshot,
   portfolio snapshot, configuration and previous state; it is unique in SQLite,
   so replaying cannot duplicate a decision or a reservation.
4. **Gates fail closed.** A mandatory feed that is stale, missing or conflicting
   forces `ABSTAIN` / `DATA HALT`; nothing is estimated to keep the dashboard alive.
5. **No composite scores.** Each gate is shown separately; there is no confidence
   percentage because no validated method exists to produce one.
6. **Unconfigured is not zero.** Missing thresholds render `NOT CONFIGURED` and
   cannot certify a setup.
7. **Core BTC is untouchable.** Only tactical inventory can be sized, and the
   available balance is reduced by floors and live reservations.
8. **Proposals are not fills.** Approving reserves inventory; only a recorded fill
   changes balances; a cancellation releases nothing until confirmed.
9. **Conditions encode a sequence, not a static inequality.** "Failed reclaim"
   requires price to have tested the boundary and been rejected. Merely trading
   below value is not distribution.
10. **The value-area horizon is a measurement convention, not an edge.** The
   reclaim/breakdown test is judged against a rolling 24-hour profile
   (`analysis.value_area_hours`, configurable); the 168-hour profile is displayed
   as context and never gates a decision.

## Layout

```
config/research-seed.json   illustrative, unvalidated profile
cortex/domain.py            enums, snapshots, hashing, Decimal helpers
cortex/config.py            immutable versioned configuration
cortex/ledger.py            event-sourced dual inventory + reservations
cortex/modules.py           Module A, Module B, regime classification
cortex/allocation.py        Module C sizing and binding constraints
cortex/authority.py         Module D, ten gates, verdict, plain English
cortex/market.py            Binance public ingestion + derived features
cortex/store.py             SQLite: events, decisions, orders, config versions
cortex/app.py               orchestration services
cortex/server.py            loopback HTTP API + static dashboard
cortex/web/                 dashboard (vanilla ES modules, no build)
tests/                      39 tests covering ledger, authority and governance
```

## Tests

```powershell
python -m pytest -q
```

The suite encodes the handover's acceptance rules: reservation double-spend,
partial fills, unconfirmed cancellation, protected core, floors, unfunded BUY,
unsupported SELL, stale data, unreconciled ledger, module conflict and replay
determinism.

## Data honesty

- Spot price, OHLCV, taker-split delta and depth are observed from Binance's
  public API and marked `VERIFIED`; derived structure (support, value area, SMA)
  is marked as `derived:` in its provenance.
- Absorption is a **proxy** computed from close location and aggressor volume —
  it is labelled `PROXY` and cannot alone confirm a setup.
- Confirmed liquidation data has no trustworthy free public source here, so it is
  `UNAVAILABLE` and never blocks a decision.
- Futures open interest and funding are contextual only.

## Not in scope, by mandate

Leverage, margin, futures, shorting, borrowed capital, autonomous execution,
private exchange keys, and any claim of a proven edge.
