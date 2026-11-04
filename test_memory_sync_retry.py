"""Regression for GitHub ruleset timeout without hiding durable-write failures."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import tino_persistent_store as store


class MemorySyncRetryTests(unittest.TestCase):
    def test_ruleset_timeout_retries_whole_transaction(self):
        failure = 'github_put_403:The rule did not run because of a timeout.'
        with patch.object(store, '_sync_file_to_remote_unlocked', side_effect=[(False, failure), (True, None)]) as sync, patch.object(store.time, 'sleep') as sleep:
            self.assertEqual(store._sync_file_to_remote('ledger.jsonl'), (True, None))
        self.assertEqual(sync.call_count, 2)
        self.assertEqual(sync.call_args_list[0], sync.call_args_list[1])
        sleep.assert_called_once_with(2)

    def test_real_denial_and_validation_failure_never_retry(self):
        for error in ('github_put_403:Resource not accessible by integration',
                      'github_put_403:Repository rule violations found',
                      'github_put_401:Bad credentials', 'github_put_422:Invalid request',
                      'remote_verify_mismatch:prediction_log.jsonl'):
            with self.subTest(error=error), patch.object(store, '_sync_file_to_remote_unlocked', return_value=(False, error)) as sync, patch.object(store.time, 'sleep') as sleep:
                self.assertEqual(store._sync_file_to_remote('ledger.jsonl'), (False, error))
                self.assertEqual(sync.call_count, 1)
                sleep.assert_not_called()

    def test_transient_failures_are_bounded_and_remain_failed(self):
        for error in ('github_put_409:SHA mismatch', 'github_put_503:Unavailable', 'github_put_599:TimeoutError'):
            with self.subTest(error=error), patch.object(store, '_sync_file_to_remote_unlocked', return_value=(False, error)) as sync, patch.object(store.time, 'sleep') as sleep:
                self.assertEqual(store._sync_file_to_remote('ledger.jsonl'), (False, error))
                self.assertEqual(sync.call_count, 3)
                self.assertEqual([c.args[0] for c in sleep.call_args_list], [2, 4])

    def test_failed_file_does_not_block_other_ledgers_and_manifest_is_truthful(self):
        with tempfile.TemporaryDirectory() as folder:
            paths=[Path(folder)/'prediction_log.jsonl', Path(folder)/'scan_runs.jsonl']
            for path in paths: path.write_text('{"frozen": true}\n')
            manifest=Path(folder)/'output'/'memory_sync_result.json'
            with patch.object(store, '_sync_file_to_remote', side_effect=[(False, 'github_put_403:denied'), (True, None)]) as sync:
                result=store.sync_memory_batch(paths, manifest)
            self.assertEqual(sync.call_count, 2)
            self.assertEqual(result['status'], 'FAIL')
            self.assertEqual([x['verified'] for x in result['files']], [False, True])
            self.assertEqual(json.loads(manifest.read_text()), result)
            self.assertEqual(paths[0].read_text(), '{"frozen": true}\n')

    def test_exception_preserves_manifest_and_continues(self):
        with tempfile.TemporaryDirectory() as folder:
            paths=[Path(folder)/'a.jsonl', Path(folder)/'b.jsonl']
            for p in paths: p.write_text('{}\n')
            with patch.object(store, '_sync_file_to_remote', side_effect=[OSError('test'), (True,None)]):
                result=store.sync_memory_batch(paths, Path(folder)/'manifest.json')
            self.assertEqual(result['status'], 'FAIL')
            self.assertEqual(result['files'][0]['error'], 'OSError')
            self.assertTrue(result['files'][1]['verified'])

    def test_probe_uses_unique_non_trading_file_and_fails_on_unverified_write(self):
        import verify_memory_sync_v1118 as probe
        import memory_store
        with tempfile.TemporaryDirectory() as folder, patch.object(memory_store, 'MEMORY_DIR', Path(folder)), patch.dict('os.environ', {'GITHUB_RUN_ID':'12345', 'GITHUB_RUN_ATTEMPT':'2', 'GITHUB_STEP_SUMMARY':''}), patch.object(store, '_remote_config', return_value={'configured':True,'token':'test'}), patch.object(store, '_sync_file_to_remote', return_value=(False, 'test denial')) as sync:
            with self.assertRaises(SystemExit):
                probe.main()
            path=sync.call_args.args[0]
            self.assertEqual(path, Path(folder)/'health_checks'/'12345-2.json')
            self.assertIn('not a forecast', json.loads(path.read_text())['purpose'])
            self.assertFalse((Path(folder)/'prediction_log.jsonl').exists())


if __name__ == '__main__':
    unittest.main()
