# TINO V1096｜Single Decision Lifecycle Audit

Baseline: V1092 `4644d354dafa2903c136ae4ea441862542f05ecb`.

## V1093 — Single decision owner

- `orchestrator.orchestrate()` creates one frozen `DecisionSnapshot`.
- `ui_v9_battle_panel.py` only renders that snapshot and blocks if it is absent.
- The prediction log stores the exact public action, situation, entry plan and funnel.
- `V9_GOLDEN_MASTER_CONTRACT_V1093.json` freezes the V9 three-layer UI contract.
- `memory_policy_v1093.py` keeps Public read-only and Admin writes approval-gated.

## V1094 — Typed evidence registry

- Evidence has an ID, family, correlation group, numeric value, unit, source,
  date, freshness, state and verification flags.
- Missing evidence is `UNKNOWN`, never zero or neutral.
- The gate reads every accepted fact; the UI only displays the top three.
- One correlation group can contribute at most one vote in each family/direction.
- TW margin/institutional evidence and US short-interest evidence remain separate.

## V1095 — Recovery lifecycle

States are session-aware: `OBSERVING`, `DELEVERAGING`, `STABILIZING`,
`REBOUNDING`, `PULLBACK`, `RECLAIMED`, `ENTRY_ARMED`, `ENTRY_TRIGGERED`,
`FAILED`, `UNKNOWN`.

A single OHLC snapshot cannot claim that a pullback-reclaim sequence happened.
Recovery entry requires a prior recorded lifecycle state and a verified transition.
Runtime state is not written by Public; formal prediction logging persists it.

## V1096 — Public action audit and replay

- Audit records the action actually shown on the Web, not only V1077 shadow action.
- Metrics include T+1 return, MFE, MAE, stop touch, missed rebound and
  risk-adjusted quality.
- Replay supports T+1/T+3/T+5 horizons and aggregates the blocking funnel.
- Success is evaluated by risk-adjusted quality, not by increasing BUY frequency.
- No replay or audit result directly changes formal model weights.

## Preserved contracts

- V9 panel layout, height and three information layers.
- Formal T0/T1/High/Low generation and reconstructable trace.
- ABC as context rather than a Truth override.
- Truth Guard before public output.
- TW/US/ETF market-specific evidence routing.
- No ticker-specific hardcode.
- All learning changes still require Tino Admin approval.
