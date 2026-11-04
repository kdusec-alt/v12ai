"""Small real CI durability probe. No forecasts, market data, AI or paper orders."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re


def main():
    from tino_persistent_store import _sync_file_to_remote, _remote_config, _github_read_file, _atomic_write_bytes
    from memory_store import MEMORY_DIR
    cfg = _remote_config()
    if not cfg.get('configured') or not cfg.get('token'):
        raise SystemExit('Memory check requires configured authenticated GitHub storage')
    run_id = os.environ.get('GITHUB_RUN_ID', '')
    attempt = os.environ.get('GITHUB_RUN_ATTEMPT', '1')
    if not re.fullmatch(r'\d+', run_id) or not re.fullmatch(r'\d+', attempt):
        raise SystemExit('This probe requires a GitHub Actions run identity')
    path = Path(MEMORY_DIR) / 'health_checks' / f'{run_id}-{attempt}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        'purpose': 'durability probe only; not a forecast or trade',
        'run_id': run_id, 'attempt': attempt,
        'commit': os.environ.get('GITHUB_SHA', ''),
        'checked_at': datetime.now(timezone.utc).isoformat(),
    }, indent=2), encoding='utf-8')
    ok, error = _sync_file_to_remote(path, shrink_guard=True)
    if not ok:
        raise SystemExit(f'Memory write/read-back check failed: {error}')
    # Exercise the real ledger payloads too: a tiny marker alone would not
    # reproduce a ruleset timeout caused by a larger prediction upload.
    # Start from the current remote bytes; never seed these files from main's
    # older checkout. The normal sync transaction preserves concurrent appends.
    for name in ('prediction_log.jsonl', 'paper_lab/scan_runs.jsonl'):
        exists, blob, _sha, read_error = _github_read_file(name)
        if not exists or blob is None or read_error:
            raise SystemExit(f'Memory ledger read failed: {name}: {read_error}')
        local = Path(MEMORY_DIR) / name
        _atomic_write_bytes(local, blob)
        ok, error = _sync_file_to_remote(local, shrink_guard=True)
        if not ok:
            raise SystemExit(f'Memory ledger write/read-back failed: {name}: {error}')
        print(f'PASS: {name} current remote records preserved and read-back verified ({len(blob)} bytes)')
    print('PASS: GitHub memory write and exact read-back verified; no forecasts or trades created')
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as handle:
            handle.write('GitHub memory write and exact read-back: **PASS**. No market requests or AI calls.\n')


if __name__ == '__main__':
    main()
