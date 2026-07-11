RC2.3 No-Lock Hotfix 2026-07-09

Scope:
- Only backend Time/Price Guard and Learning write protection were changed.
- V9 frontend UI, tactical language, layout and information flow were not redesigned.

Changes:
1. Taiwan market time is normalized through Asia/Taipei session phase.
   - 09:00 <= now < 13:30: intraday
   - 13:30 <= now <= 13:35: close_confirm
   - after 13:35: after_close
   - 13:30:01 quotes are no longer treated as stale intraday data.

2. Taiwan price guard no longer hard-locks the entire analysis when all real sources are delayed.
   - It uses the best real delayed quote as 延遲參考.
   - decision_blocked remains False for delayed-reference mode.
   - limited_price_mode=True and price_verified=False are stored in Admin/meta.

3. Learning protection added.
   - limited_price_mode or decision_blocked forecasts are not written as official Learning samples.
   - This avoids polluting Auto-Learning while still allowing the front analysis to run.

Validation:
- python -m py_compile *.py: PASS
- smoke_tests.py: all functional tests passed until pre-existing test_file_size_guard.
- test_file_size_guard fails because data_sources_tw.py is already above 700 lines in the uploaded baseline.
