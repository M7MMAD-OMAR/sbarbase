"""Reject unauthenticated recovery data before any restore consumer can use it."""
import base64
import unittest
from cryptography.exceptions import InvalidTag
from recovery_bundle import seal, open_bundle, catalog_ownership
import sqlite3
import tempfile
from pathlib import Path

class RecoveryBundleTests(unittest.TestCase):
    def test_roundtrip_and_wrong_key(self):
        payload={'credentials':{'secret':'private'},'database':'payload'}
        envelope=seal(payload,b'a'*32)
        self.assertEqual(open_bundle(envelope,b'a'*32),payload)
        self.assertNotIn('private',str(envelope))
        with self.assertRaises(InvalidTag):open_bundle(envelope,b'b'*32)

    def test_ownership_names_the_hierarchy_of_a_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'control.sqlite'
            with sqlite3.connect(path) as database:
                database.executescript("""CREATE TABLE organizations(id TEXT,name TEXT);CREATE TABLE projects(id TEXT,organization TEXT,name TEXT);
                  CREATE TABLE environments(id TEXT,project TEXT,name TEXT);CREATE TABLE provision_jobs(environment TEXT,runtime TEXT);
                  INSERT INTO organizations VALUES ('o','Client');INSERT INTO projects VALUES ('p','o','Shop');
                  INSERT INTO environments VALUES ('v','p','production');INSERT INTO provision_jobs VALUES ('v','e_'||printf('%024d',0));""")
            self.assertEqual(catalog_ownership(path,'e_'+'0'*24),{'organization':{'id':'o','name':'Client'},
                'project':{'id':'p','name':'Shop'},'environment':{'id':'v','name':'production'}})
            self.assertIsNone(catalog_ownership(path,'e_'+'1'*24))
            self.assertIsNone(catalog_ownership(Path(directory)/'absent.sqlite','e_'+'0'*24))

    def test_corruption_and_format_rejected(self):
        envelope=seal({'database':'payload'},b'a'*32)
        cipher=bytearray(base64.b64decode(envelope['ciphertext']));cipher[-1]^=1
        with self.assertRaises(InvalidTag):open_bundle({**envelope,'ciphertext':base64.b64encode(cipher).decode()},b'a'*32)
        with self.assertRaises(ValueError):open_bundle({**envelope,'version':3},b'a'*32)

    def test_unknown_fields_and_invalid_nonce_rejected(self):
        envelope=seal({},b'a'*32)
        with self.assertRaises(ValueError):open_bundle({**envelope,'extra':'untrusted'},b'a'*32)
        with self.assertRaises(ValueError):open_bundle({**envelope,'nonce':'YQ=='},b'a'*32)

class RecoveryReaderTests(unittest.TestCase):
    def test_subprocess_reader_stops_at_payload_limit(self):
        import importlib.util
        from pathlib import Path
        from unittest.mock import patch
        spec=importlib.util.spec_from_file_location('recovery_export_test',Path(__file__).with_name('recovery-export.py'))
        exporter=importlib.util.module_from_spec(spec);spec.loader.exec_module(exporter)
        with patch.object(exporter,'MAX_PAYLOAD',64):
            self.assertEqual(exporter.binary(['/usr/bin/python3','-c','print("ok",end="")']),b'ok')
            with self.assertRaisesRegex(RuntimeError,'payload budget'):
                exporter.binary(['/usr/bin/python3','-c','import sys;sys.stdout.buffer.write(b"x"*65)'])
