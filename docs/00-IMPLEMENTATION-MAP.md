# CORTEX-BTC — Initial Implementation Map

**Status:** Pivoted to a signal-only product. Portfolio, allocation and order
lifecycle are out of scope and preserved in git at `8b2ee6d`.
**Reference:** CORTEX-ANTIGRAVITY-HANDOVER-001 v1.0 (G2), brief G1, architecture diagram G3, D2 (strategy input).
**Date:** 2026-11-17

## 0. Scope decision (latest)

The product publishes a **market stance** — QUALIFIED BUY ZONE, QUALIFIED SELL
ZONE, NO SETUP, CONFLICT or DATA HALT — for a small private group. It no longer
holds balances, sizes positions, or manages orders.

Removed from the product: dual-inventory ledger, allocation controller, position
sizing, order lifecycle, conversion cycles, portfolio operating states.
Retained: data integrity gate, regime, Module A, Module B, independent
classification of DISTRIBUTION vs BREAKDOWN, conflict resolution, audit.

Why the retention list is not negotiable: without a data gate the stance is
unfounded; without conflict resolution the two modules compete for authority;
without the test-and-rejection requirement "distribution" fires on any price
below value.

## 0b. As built

Runs with `python -m cortex.server`: six gates, one stance, single-screen
dashboard, configuration versioning, decision records with replay hashes,
provenance for every feature. 20 tests pass.

---

## 1. What was inspected

| Item | Finding |
|---|---|
| `G1.txt` | Brief: architecture-first, spot-only, no autonomous execution, UNVALIDATED, challenge weaknesses. |
| `G2.txt` | 1,640-line handover: 4 components, 10 gates, 2 loops, 3 sell mechanisms, 9 portfolio states, 8 screens, 23 acceptance tests. |
| `G3.jpg` | Confirms four-component topology, bearish path as an independent (not inverted) mirror of Module A, single Module D verdict bar. |
| `btc-intelligence-v4` repo | Existing V4 intelligence pipeline: JSON-schema data contracts, quality/freshness policy, config versioning, validation scripts (Node `.mjs`), Python domain models. 5 commits, foundation phase only, **no app, no UI, no market ingestion**. |
| `btc-brain-master-compilation-v9.md` + 69-module archive | Strategy knowledge base (modules 01–62, ICDM, AEGIS-3, PCF3, telemetry trust). Source material for *threshold selection*, not executable code. |
| Runtime | Python 3.11.9, pytest/pandas/pydantic available. **Node.js absent** → no Vite/React build step. Public Binance REST reachable (no keys needed). |

## 2. Hard constraint discovered

`docs/governance/V4_CONSTITUTION.md` states the packet "must never contain automatic buy/sell recommendations", and the README forbids "deterministic trading verdicts". CORTEX's Module D exists precisely to emit BUY/HOLD/SELL/ABSTAIN.

**Consequence:** CORTEX-BTC becomes a **separate repository**. It reuses V4's *patterns* (quality statuses, freshness policy, config versioning, schema-first contracts, secret scanning) but not its governance or its packet. Merging the two would violate the V4 constitution.

## 3. Reuse / build / gap

**Reuse (patterns, re-expressed in CORTEX types)**
- Quality status vocabulary and freshness thresholds — extended to the spec's six statuses (VERIFIED / PARTIALLY VERIFIED / PROXY / STALE / UNAVAILABLE / CONFLICTING).
- `observed_at` vs `collected_at` separation; UTC storage.
- Fail-closed data gate; mandatory-coverage policy.
- Config version + hash + change record.
- Schema-first validation and reproducibility tests.

**Build new**
- Dual-inventory ledger with reservations (BTC/USDT × total/available/reserved/core/tactical).
- Deterministic evidence engine for Module A and Module B, with per-condition PASS / FAIL / UNAVAILABLE / NOT_CONFIGURED.
- Allocation controller with a *binding-constraint* labelling (never a silent quantity).
- Ten-gate decision authority + decision snapshot + replay.
- Conversion-cycle ledger (Loop A / Loop B, ΔBTC, ΔUSDT).
- Order lifecycle with human-only state transitions.
- The 8-screen dashboard.

**Data gaps (must show as UNAVAILABLE, not invented)**
- Confirmed liquidations: no trustworthy free public feed; must be optional context and must not gate decisions.
- Spot CVD / footprint / absorption: derivable from public aggTrades — real, but rate-limited and needs caching.
- Volume profile / VAH / VAL / POC: computed, therefore PROXY-class derivable, never "observed".
- Futures OI / funding / taker imbalance: public endpoints, contextual only.
- Macro liquidity: out of scope for v1 → UNAVAILABLE.

## 4. Technology decision

| Layer | Choice | Why |
|---|---|---|
| Core engine | Pure Python, no third-party imports | Deterministic, testable, replayable |
| Numeric type | `Decimal` throughout | Float drift is a correctness bug in a ledger |
| Persistence | SQLite (WAL) + append-only event log | Single file, no server, transactional reservations |
| HTTP | `http.server.ThreadingHTTPServer` | Works today; no `uvicorn`, no Node build step |
| UI | Vanilla ES modules + CSS, no framework | Zero build; survives offline; matches the dark terminal aesthetic of G3 |
| Tests | pytest | Already installed; encodes the 23 acceptance tests |

**Not chosen:** React/Vite (no Node), pandas pipelines inside the decision path (nondeterminism), any LLM call inside the verdict (forbidden by §16).

## 5. Core design commitments

1. **Decision = pure function.** `decide(market_snapshot, portfolio_snapshot, config_version, open_orders)`. No clock, no network, no randomness inside. Timestamps are inputs.
2. **Content-addressed snapshots.** `snapshot_id = sha256(canonical_json)`; a decision record stores the hashes. Same hashes + same config ⇒ byte-identical verdict. Snapshot + config hash is a unique key, so re-processing cannot duplicate a decision or a reservation.
3. **Gates fail closed.** Any mandatory feed UNAVAILABLE/STALE/CONFLICTING ⇒ Gate 1 fails ⇒ ABSTAIN / DATA HALT. No fallback to a "best guess".
4. **No composite scores.** Gate results are shown individually. No weighted confidence number unless a specified, validated method exists (there is none).
5. **No invented numbers.** Every threshold has an explicit `NOT CONFIGURED` state, and a module in that state cannot reach CONFIRMED.
6. **Reservations are the only path to a quantity.** Proposed size = `min(` all constraint candidates `)` plus the identity of the binding constraint.
7. **Orders never assume outcomes.** PARTIALLY FILLED moves only the filled quantity; CANCELLED releases nothing until confirmation.
8. **Core BTC is untouchable by default.** A sell that would breach the core or the inventory floor is rejected at Gate 7, not silently clipped.

## 6. Module semantics

**Module A / B** evaluate an ordered confirmation sequence (spec §7). Each step yields PASS / FAIL / UNAVAILABLE / NOT_CONFIGURED. Module state derives deterministically:

```
DATA HALT     if any mandatory input is not VERIFIED
INVALIDATED   if any invalidation condition is TRUE
ELIGIBLE      if all required steps PASS and entry economics acceptable
CONFIRMED     if E1..E6 PASS and price is at/inside the entry zone
SETUP         if the structural test occurred and >= 3 steps PASS
WATCH         if the setup exists but confirmation is incomplete
REVIEW        if evidence conflicts or is materially ambiguous
```

Module B additionally emits `PROFIT-TAKING` / `RISK REDUCTION` / `DEFENSIVE EXIT` from the config, never inferred.

**Module C** computes, per direction, the constraint set — available inventory, floors, core protection, sleeve limit, exposure band, max trade size, risk budget at invalidation, fees, slippage, open reservations — and returns `min()` with the binding constraint.

**Module D** runs Gates 1→10. Verdict table is explicit (spec §14), including the "qualified but unfunded" case that must never become an unfunded BUY.

## 7. Data model (initial)

`MarketSnapshot`, `PortfolioSnapshot`, `ModuleAssessment`, `AllocationAssessment`, `DecisionRecord`, `OrderRecord`, `FillRecord`, `ConversionCycle`, `ConfigurationRecord`, `EventLogEntry`.

Each is append-only where it represents history; portfolio state is derived by replaying events, never mutated in place. That gives free auditability and reconciliation.

## 8. Screens and navigation

| # | Screen | Priority |
|---|---|---|
| 1 | Command Centre (verdict, portfolio, A/B side-by-side, C reconciliation) | P0 |
| 2 | Accumulation & Reversal (Module A) | P1 |
| 3 | Distribution & Breakdown (Module B) | P1 |
| 4 | Portfolio & Allocation | P0 |
| 5 | Conversion Cycles & Orders | P1 |
| 6 | Market Telemetry | P2 |
| 7 | Audit, Research & Performance | P2 |
| 8 | Configuration & Governance | P0 |

Layout rule from §15: Decision → Position → Evidence → Plain English → Transaction → Audit. Verdict is the visually dominant element; G3's three-band topology becomes the Command Centre's spine.

## 9. Test plan (maps to §20)

Ledger: double-spend of a reservation, partial fill accounting, unconfirmed cancel, floor breach, core protection, negative balance impossibility.
Authority: A-qualified/B-deteriorating, both-qualified conflict, stale CVD, missing spot data, unreconciled balances, unfunded BUY, unsupported SELL.
Determinism: same snapshot twice → one decision, no duplicate reservation; replay → identical verdict.
Governance: config change is versioned with before/after and operator; no parameter can be edited without a new version.

## 10. Where this stands

| Stage | Deliverable | State |
|---|---|---|
| 1 | Config versioning, data quality gate, audit log | built |
| 2 | Modules A and B, independent evidence matrices, state machine | built |
| 3 | Conflict resolution, one stance, replay hashes | built |
| 4 | Single-screen dashboard, provenance, export | built |
| 5 | Historical base rates — how often each stance fires, and what followed | **next, and the one that decides whether this is valuable** |
| 6 | Backtest with fees, slippage, unfilled conditions, benchmark comparison | not started |

Stage 5 is not optional decoration. A stance with no measured history is a
well-typeset opinion, and this product is for other people.

## 11. Risks I am flagging now

1. **Inert dashboard risk.** With every threshold NOT CONFIGURED, Gates 4–5 legitimately fail and the system will sit in REVIEW/HOLD. Correct, but it looks broken. Mitigation: ship an explicitly labelled *illustrative research profile* (config version `research-seed-1`, UNVALIDATED) so the pipeline is demonstrable, while an empty profile remains the default.
2. **Threshold provenance.** The 69-module knowledge base contains specific Fibonacci/ICDM/PCF3 values. Importing them would be inventing validated parameters. They enter as *candidate, unvalidated* config with citations, never as defaults.
3. **CVD realism.** Binance aggTrades give real aggressor-side volume, but "absorption" and "bid replenishment" are derived claims. They will be labelled PROXY and cannot alone confirm a setup.
4. **Liquidation claims.** Nothing in the system may present a heatmap estimate as confirmed liquidations.
5. **Rate limits.** Public endpoints throttle; ingestion needs a cache with per-feed freshness SLA, which Gate 1 then enforces honestly.
6. **Scope.** This is a multi-week build. The vertical slice (Phases 1–4 + Command Centre) is the meaningful first milestone; the remaining screens are additive.

## 12. Adjustments made after the first live run

1. **Value-area horizon 24h, configurable** (`analysis.value_area_hours`). The
   original 168h profile put VAH ~12% above price in a downtrend, so Module A's
   reclaim condition could never pass. 24h keeps the test tied to the current
   auction. The 168h profile remains visible as context and never gates.
2. **Failed reclaim now requires a test.** Module B previously passed whenever
   price was below VAH — which is almost always — so the condition was a free
   pass. It now requires recent price to have touched VAH and been rejected.
3. **Portfolio snapshot identity is content-based.** It previously hashed the
   wall clock, which broke replay determinism; caught by an intermittent test.

## 13. Open questions for the operator

1. Portfolio figures: real balances or paper balances first? (Guide: paper, so no invented holdings.)
2. Exchange venue for public data: Binance primary, Coinbase as the independent cross-check (spec wants a cross-check source)?
3. Is an illustrative research profile acceptable to make the engine demonstrable before real thresholds exist?
