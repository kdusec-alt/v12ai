# TINO V12 RC24.2 Post-Render Crash Guard

## Root cause isolated from the uploaded source

`data_sources_tw_fundamental._run_sources_fast()` used a `ThreadPoolExecutor` and then called:

```python
ex.shutdown(wait=False, cancel_futures=True)
```

Already-running HTTP tasks cannot be cancelled. The function could return, Streamlit could finish displaying the forecast, while MoneyDJ/Goodinfo/Yahoo/Anue workers continued downloading/parsing in the background. This matches the reported timing: **query succeeds → page displays → backend crashes**.

## RC24.2 changes

- Removes the non-cancellable fundamental background thread pool.
- Normal foreground path uses only MOPS + FinMind.
- Deep public-site cross-check becomes explicit opt-in (`TINO_FUND_DEEP_CROSSCHECK=1`).
- Removes full `FinalForecast` duplication in `st.cache_data`; session state is the only forecast holder.
- Raw prediction JSONL still writes, but backup/ledger mirror is off in the foreground by default.
- Adds `/tmp/tino_runtime_trace.jsonl` stage markers with peak RSS and thread count.
- Does not modify `data_sources_tw.py`, Price Guard, orchestrator, or V9 UI.

## Safe defaults

- `TINO_FUND_DEEP_CROSSCHECK=0`
- `TINO_INLINE_REMOTE_SYNC=0`
- `TINO_INLINE_MEMORY_MIRROR=0`
