"""Disk-backed JSONL union; memory use depends on one row, not ledger size."""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile


def file_digest(path):
    p=Path(path)
    if not p.is_file():return None
    h=hashlib.sha256()
    with p.open('rb') as handle:
        for block in iter(lambda:handle.read(128*1024),b''):h.update(block)
    return h.digest()


def merge_streams(streams, destination, identity, stamp):
    destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='tino-merge-',dir=destination.parent) as folder:
        connection=sqlite3.connect(str(Path(folder)/'rows.db'))
        counts=[];seq=0
        try:
            connection.execute('PRAGMA cache_size=-1024')
            connection.execute('PRAGMA temp_store=FILE')
            connection.execute('CREATE TABLE rows (identity TEXT PRIMARY KEY, stamp TEXT, seq INTEGER, body TEXT)')
            for stream in streams:
                count=0
                for line in stream:
                    try:row=json.loads(line)
                    except (ValueError,UnicodeError):continue
                    if not isinstance(row,dict) or not row:continue
                    key=identity(row) or f'row:{seq}'
                    body=json.dumps(row,ensure_ascii=False,default=str)+'\n'
                    connection.execute('INSERT OR REPLACE INTO rows VALUES (?,?,?,?)',(key,stamp(row),seq,body))
                    seq+=1;count+=1
                counts.append(count)
            connection.commit()
            digest=hashlib.sha256();merged=0;size=0
            with destination.open('wb') as handle:
                for (body,) in connection.execute('SELECT body FROM rows ORDER BY stamp, seq'):
                    data=body.encode('utf-8');handle.write(data);digest.update(data);size+=len(data);merged+=1
            return {'counts':counts,'merged_rows':merged,'bytes':size,'digest':digest.digest()}
        finally:connection.close()
