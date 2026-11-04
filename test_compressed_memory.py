"""Lossless large-ledger transport with legacy history preserved."""
import base64
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import tino_persistent_store as store


class CompressedMemoryTests(unittest.TestCase):
    def test_legacy_reader_is_unchanged_when_no_compressed_copy(self):
        original=(True,b'{"id":"a"}\n','old-sha',None)
        with patch.object(store,'_github_read_remote_path',side_effect=[original,(False,None,None,'remote_missing')]):
            self.assertEqual(store._github_read_file('prediction_log.jsonl'),original)

    def test_compressed_and_legacy_records_are_both_preserved(self):
        old=b'{"id":"old","value":1}\n'
        new=b'{"id":"new","value":2}\n'
        with patch.object(store,'_github_read_remote_path',side_effect=[(True,old,'old',None),(True,gzip.compress(new),'packed',None)]):
            ok,blob,sha,error=store._github_read_file('prediction_log.jsonl')
        self.assertTrue(ok);self.assertIsNone(error);self.assertEqual(sha,'packed')
        self.assertEqual({r['id'] for r in store._decode_jsonl_bytes(blob)},{'old','new'})

    def test_corrupt_or_unreadable_compressed_copy_never_falls_back_to_stale_history(self):
        for packed in [(True,b'broken','packed',None),(False,None,None,'github_get_403:denied')]:
            with self.subTest(packed=packed),patch.object(store,'_github_read_remote_path',side_effect=[(True,b'{"id":"old"}\n','old',None),packed]):
                result=store._github_read_file('prediction_log.jsonl')
            self.assertFalse(result[0]);self.assertIsNone(result[1]);self.assertTrue(result[3])

    def test_real_sync_compresses_large_ledger_and_verifies_all_rows(self):
        cfg={'configured':True,'repo':'test/repo','branch':'tino-memory','token':'test','memory_dir':'.tino_memory'}
        canonical='.tino_memory/prediction_log.jsonl'
        old=b'{"id":"old","value":"preserved"}\n'
        remote={canonical:old}
        puts=[]
        def read(path,**kwargs):
            if path not in remote:return False,None,None,'remote_missing'
            return True,remote[path],'sha-'+path,None
        def request(method,url,token,payload=None):
            self.assertEqual(method,'PUT')
            self.assertTrue(url.endswith(canonical+'.gz'))
            puts.append(payload)
            remote[canonical+'.gz']=base64.b64decode(payload['content'])
            return 201,{}
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'prediction_log.jsonl'
            text='x'*(8*1024*1024)
            path.write_text(json.dumps({'id':'new','text':text})+'\n')
            with patch.object(store,'_remote_config',return_value=cfg),patch.object(store,'_ensure_remote_branch',return_value=(True,None)),patch.object(store,'_github_read_remote_path',side_effect=read),patch.object(store,'_gh_request',side_effect=request):
                ok,error=store._sync_file_to_remote(path)
            self.assertTrue(ok,error)
            self.assertEqual(remote[canonical],old) # Original archive is untouched.
            rows=store._decode_jsonl_bytes(gzip.decompress(remote[canonical+'.gz']))
            self.assertEqual({r['id'] for r in rows},{'old','new'})
            self.assertEqual(next(r for r in rows if r['id']=='new')['text'],text)
            self.assertLess(len(remote[canonical+'.gz']),len(text)//100)
            self.assertEqual(len(puts),1)
            self.assertNotIn('sha',puts[0]) # New compressed path cannot use legacy SHA.

    def test_concurrent_writer_causes_retry_instead_of_overwrite(self):
        cfg={'configured':True,'repo':'test/repo','branch':'tino-memory','token':'test','memory_dir':'.tino_memory'}
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'prediction_log.jsonl';path.write_text('{"id":"new"}\n')
            with patch.object(store,'_remote_config',return_value=cfg),patch.object(store,'_ensure_remote_branch',return_value=(True,None)),patch.object(store,'_github_read_file',return_value=(True,b'{"id":"concurrent"}\n','changed-sha',None)),patch.object(store,'_gh_request') as request:
                ok,error=store._github_write_file(path,payload_bytes=path.read_bytes(),expected_sha='previous-sha')
            self.assertFalse(ok);self.assertIn('github_put_409:',error)
            request.assert_not_called()


if __name__=='__main__':unittest.main()
