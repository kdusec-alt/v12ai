"""Real cold memory-restore smoke check; reads only, no market or AI requests."""
import json
import resource
import time
from unittest.mock import patch
import tino_persistent_store as store


def main():
    started=time.monotonic()
    # Any accidental write is a test failure; existing branch and records stay intact.
    with patch.object(store,'_sync_file_to_remote',side_effect=AssertionError('Read-only startup attempted a remote write')):
        report=store.ensure_memory_initialized_bootsafe(migrate=False,read_only=True)
    remote=report.get('remote_restore') or {}
    summary={'status':remote.get('status'),'restored_files':len(remote.get('restored_files') or []),
             'deferred_writes':len(remote.get('pending_sync') or []),
             'seconds':round(time.monotonic()-started,2),
             'peak_rss_mb':round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,2),
             'warnings':remote.get('shrink_warnings') or []}
    print(json.dumps(summary,ensure_ascii=False))
    if remote.get('status') not in {'PASS','EMPTY_REMOTE'}:
        raise SystemExit('Cold memory restore did not fully pass')
    print('PASS: read-only cold restore completed without market, model or remote-write requests')


if __name__=='__main__':main()
