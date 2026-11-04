import io
import json
from pathlib import Path
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch
import tino_persistent_store as store
from memory_streaming_v1118 import merge_streams


class MemoryStreamingTests(unittest.TestCase):
    def test_disk_union_matches_existing_id_and_sort_semantics(self):
        remote=[{'id':'a','created_at':'2','v':1},{'id':'b','created_at':'1','v':2}]
        local=[{'id':'a','created_at':'2','v':3},{'id':'c','created_at':'2','v':4}]
        expected=store._encode_jsonl_rows(store._merge_jsonl_rows(remote,local))
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'union.jsonl'
            merge_streams([io.BytesIO(store._encode_jsonl_rows(remote)),io.BytesIO(store._encode_jsonl_rows(local))],target,store._memory_row_identity,store._memory_row_stamp)
            self.assertEqual(target.read_bytes(),expected)

    def test_large_reconcile_keeps_all_rows_with_bounded_python_heap(self):
        remote=b''.join(json.dumps({'id':str(i),'text':'x'*4096}).encode()+b'\n' for i in range(1200))
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'prediction_log.jsonl';p.write_text('{"id":"extra","value":2}\n')
            tracemalloc.start()
            try:
                ok,info,error=store._reconcile_local_with_remote(p,remote)
                _,peak=tracemalloc.get_traced_memory()
            finally:tracemalloc.stop()
            self.assertTrue(ok,error);self.assertEqual(info['merged_rows'],1201)
            self.assertEqual(info['merge_backend'],'disk_streaming')
            self.assertLess(peak,8*1024*1024)
            again=store._reconcile_local_with_remote(p,remote)
            self.assertTrue(again[0]);self.assertFalse(again[1]['changed'])

    def test_backup_rejects_partial_or_smaller_source(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'data.jsonl';p.write_text('{"id":"a"}\n{"id":"b"}\n')
            store._write_local_backup(p)
            backup=p.parent/'_backup'/'data.jsonl.bak';saved=backup.read_bytes()
            for value in ('{"id":"a"}\n','{"id":"a"}\nbroken'):
                p.write_text(value);store._write_local_backup(p)
                self.assertEqual(backup.read_bytes(),saved)

    def test_read_only_boot_does_not_seed_or_write_remote(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(store._BOOTSAFE_INIT_CACHE,{},clear=True),patch.object(store,'remote_restore_memory_files',return_value={'configured':True,'status':'PASS','missing_files':['prediction_log.jsonl']}) as restore,patch.object(store,'ensure_memory_initialized',return_value={}),patch.object(store,'_sync_file_to_remote') as sync:
            store.ensure_memory_initialized_bootsafe(path=Path(folder)/'ledger.json',read_only=True)
            restore.assert_called_once_with(force=False,sync_recovered=False)
            sync.assert_not_called()

    def test_large_json_cold_restore_preserves_original_bytes(self):
        remote=json.dumps({'recent_predictions':[{'id':'a','text':'x'*(2*1024*1024)}]}).encode()
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'tino_memory_ledger.json'
            ok,info,error=store._reconcile_local_with_remote(target,remote)
            self.assertTrue(ok,error)
            self.assertEqual(info['merge_backend'],'cold_exact_copy')
            self.assertEqual(target.read_bytes(),remote)

    def test_save_ledger_preserves_input_and_all_history_fields(self):
        import copy
        with tempfile.TemporaryDirectory() as folder:
            data=store.empty_ledger();data['recent_predictions']=[{'id':'a','nested':{'value':[1,2,3]}}]
            before=copy.deepcopy(data)
            ok,saved=store.save_ledger(data,Path(folder)/'ledger.json',sync_remote=False)
            self.assertTrue(ok)
            self.assertEqual(data,before)
            self.assertEqual(saved['recent_predictions'],before['recent_predictions'])


if __name__=='__main__':unittest.main()
