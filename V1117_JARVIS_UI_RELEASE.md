# V1117 AI 分析指揮台

Base: V1116 main 98a17cba5b1310e9f71eba42c85dbc4b89380437.

## Implemented

- Central standby core and ticker input on the analysis home page.
- Read-only command summary built from the same public snapshot/decision brief
  as the existing battle panel. Explicit unavailable prices remain unavailable.
- Four workspaces: trading plan, institutional/event evidence, deep report,
  complete original two-column view. Only the selected workspace renders.
- Comfortable single-panel typography and scrolling preserve all original fields.
- Motion reduction and responsive command grid. No new package dependencies,
  network calls, polling loops, full-market scans or worker threads.
- Optional UI import failure falls back to the original renderer.

## Validation

`python -m unittest -v test_jarvis_v1117.py`

11 tests cover snapshot immutability, missing/invalid price behavior, market
metadata, HTML escaping, stopped forecasts, unavailable entry conditions,
prediction labels and single-workspace renderer routing. Python compilation
checked for every changed source file.

## Scope

This release is the user interface stage. It does not claim simulated fills,
historical strategy returns, autonomous paper trading or a new language model.
Formal decision rules, Research Lab isolation, learning and Colab/Drive scanner
pipeline continue to own their outputs. Continuous simulation requires a
separate execution engine and durable transaction ledger.

Rollback: revert this release PR; all changes are additive UI projections or
optional renderer arguments and contain no storage migration.
