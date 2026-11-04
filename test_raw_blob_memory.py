import hashlib
import io
import urllib.error
import unittest
from unittest.mock import patch
import tino_persistent_store as store


def sha(blob):return hashlib.sha1(f'blob {len(blob)}\0'.encode()+blob).hexdigest()


class RawBlobMemoryTests(unittest.TestCase):
    def test_raw_blob_is_immutable_hash_verified_and_not_json_decoded(self):
        blob=b'{"id":"a"}\n'
        requests=[]
        def open_url(request,**kwargs):
            requests.append(request)
            self.assertEqual(request.get_header('Accept'),'application/vnd.github.raw+json')
            return io.BytesIO(blob)
        with patch('urllib.request.urlopen',side_effect=open_url):
            self.assertEqual(store._github_raw_blob('owner/repo',sha(blob),'token'),(True,blob,None))
        self.assertEqual(len(requests),1)

    def test_truncated_or_wrong_bytes_never_restore(self):
        with patch('urllib.request.urlopen',return_value=io.BytesIO(b'truncated')):
            ok,blob,error=store._github_raw_blob('owner/repo',sha(b'complete'),'token')
        self.assertFalse(ok);self.assertIsNone(blob);self.assertIn('hash_mismatch',error)

    def test_expired_token_falls_back_only_for_read(self):
        blob=b'ok';requests=[]
        def open_url(request,**kwargs):
            requests.append(request)
            if len(requests)==1:raise urllib.error.HTTPError(request.full_url,401,'expired',{},io.BytesIO())
            self.assertIsNone(request.get_header('Authorization'))
            return io.BytesIO(blob)
        with patch('urllib.request.urlopen',side_effect=open_url):
            self.assertTrue(store._github_raw_blob('owner/repo',sha(blob),'expired')[0])
        self.assertEqual(len(requests),2)


if __name__=='__main__':unittest.main()
