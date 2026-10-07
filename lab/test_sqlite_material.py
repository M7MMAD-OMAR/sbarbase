import dataclasses
import fcntl
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import sqlite_material as codec

PACKET = Path(__file__).resolve().parent


class OriginalSchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='sbarbase-original-codec-')
        self.root = Path(self.temp.name)
        self.source = self.root/'source'
        self.target = self.root/'target'
        self.source.mkdir(mode=0o700)
        self.target.mkdir(mode=0o700)
        self.bun('create',self.source)
        self.deadline = time.monotonic()+30
        self.parent = os.open(self.target,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        self.addCleanup(os.close,self.parent)
        self.addCleanup(self.temp.cleanup)

    def bun(self, operation, directory):
        result = subprocess.run([shutil.which('bun'),str(PACKET/'original_schema_fixture.ts'),operation,str(directory)],
                                stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15)
        if result.returncode:
            raise AssertionError('Original schema fixture failed; private diagnostics withheld')

    def material(self, kind, template=False):
        name = ({'catalog':'template-catalog.sqlite','managed-keys':'template-keys.sqlite'} if template else
                {'catalog':'control.sqlite','managed-keys':'managed-keys.sqlite'})[kind]
        fd=os.open(self.source/name,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            return codec.capture(kind,fd,self.deadline,max_bytes=4*1024*1024)
        finally:
            os.close(fd)

    def rows(self,path):
        db=sqlite3.connect(f'file:{path}?mode=ro',uri=True)
        try:
            tables=db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
            return {name:sorted(db.execute('SELECT * FROM "'+name.replace('"','""')+'"').fetchall(),key=repr) for name, in tables}
        finally:
            db.close()

    def test_complete_original_schema_rows_and_key_behavior_roundtrip(self):
        for kind,name in [('catalog','control.sqlite'),('managed-keys','managed-keys.sqlite')]:
            material=self.material(kind)
            result=codec.reconstruct(material,self.parent,self.material(kind,True),self.deadline)
            try:
                self.assertEqual(fcntl.fcntl(result,fcntl.F_GETFL)&os.O_ACCMODE,os.O_RDONLY)
                self.assertFalse(os.get_inheritable(result))
                self.assertEqual((self.target/name).read_bytes(),material.payload)
                self.assertEqual(self.rows(self.source/name),self.rows(self.target/name))
            finally:
                os.close(result)
        shutil.copyfile(self.source/'private-fixture.json',self.target/'private-fixture.json')
        os.chmod(self.target/'private-fixture.json',0o600)
        self.bun('verify',self.target)

    def test_writable_source_descriptor_refused(self):
        fd=os.open(self.source/'control.sqlite',os.O_RDWR)
        try:
            with self.assertRaises(codec.Refused):codec.capture('catalog',fd,self.deadline,max_bytes=4*1024*1024)
        finally:os.close(fd)

    def test_wrong_material_role_refused(self):
        with self.assertRaises(codec.Refused):
            codec.decode('catalog',self.material('managed-keys').payload,self.deadline,max_bytes=4*1024*1024)

    def test_incoming_fd_reuse_cannot_redirect_owned_capture(self):
        source=self.source/'control.sqlite'
        expected=self.material('catalog')
        incoming=os.open(source,os.O_RDONLY)
        real_pread=codec.os.pread
        replaced=[False]
        def pread(fd,size,offset):
            if not replaced[0]:
                replaced[0]=True
                self.assertNotEqual(fd,incoming)
                os.close(incoming)
                replacement=os.open(source,os.O_RDWR)
                if replacement != incoming:
                    os.dup2(replacement,incoming);os.close(replacement)
            return real_pread(fd,size,offset)
        try:
            with patch.object(codec.os,'pread',side_effect=pread):
                actual=codec.capture('catalog',incoming,self.deadline,max_bytes=4*1024*1024)
            self.assertEqual(actual,expected)
            self.assertEqual(fcntl.fcntl(incoming,fcntl.F_GETFL)&os.O_ACCMODE,os.O_RDWR)
        finally:os.close(incoming)

    def test_unknown_schema_table_refused_before_write(self):
        db=sqlite3.connect(self.source/'control.sqlite')
        db.execute('CREATE TABLE extra_payload(value BLOB)');db.commit();db.close()
        with self.assertRaises(codec.Refused):
            codec.reconstruct(self.material('catalog'),self.parent,self.material('catalog',True),self.deadline)
        self.assertEqual(list(self.target.iterdir()),[])

    def test_existing_target_not_adopted(self):
        target=self.target/'control.sqlite';target.write_bytes(b'foreign')
        with self.assertRaises(FileExistsError):
            codec.reconstruct(self.material('catalog'),self.parent,self.material('catalog',True),self.deadline)
        self.assertEqual(target.read_bytes(),b'foreign')

    def test_symlink_target_not_followed(self):
        foreign=self.root/'foreign';foreign.write_bytes(b'foreign')
        (self.target/'control.sqlite').symlink_to(foreign)
        with self.assertRaises(FileExistsError):
            codec.reconstruct(self.material('catalog'),self.parent,self.material('catalog',True),self.deadline)
        self.assertEqual(foreign.read_bytes(),b'foreign')

    def test_expired_during_capture_refused(self):
        fd=os.open(self.source/'control.sqlite',os.O_RDONLY)
        try:
            with patch.object(codec.time,'monotonic',side_effect=[0,2]):
                with self.assertRaises(codec.Refused):codec.capture('catalog',fd,1,max_bytes=4*1024*1024)
        finally:os.close(fd)

    def test_expired_after_write_removes_only_created_file(self):
        material=self.material('catalog');template=self.material('catalog',True)
        original=codec.os.fsync
        # Advance the original clock only after the first actual file fsync.
        expired=[False]
        def fsync(fd):original(fd);expired[0]=True
        def clock(_):
            if expired[0]:raise codec.Refused('expired')
        with patch.object(codec.os,'fsync',side_effect=fsync),patch.object(codec,'clock',side_effect=clock):
            with self.assertRaises(codec.Refused):codec.reconstruct(material,self.parent,template,self.deadline)
        self.assertEqual(list(self.target.iterdir()),[])

    def test_wal_main_file_refused(self):
        db=sqlite3.connect(self.source/'control.sqlite');db.execute('PRAGMA journal_mode=WAL')
        try:
            with self.assertRaises(codec.Refused):self.material('catalog')
        finally:db.close()

    def test_corrupt_database_refused(self):
        payload=self.material('catalog').payload
        with self.assertRaises(codec.Refused):codec.decode('catalog',payload[:150],self.deadline,max_bytes=len(payload))

    def test_forged_template_hash_refused(self):
        material=self.material('catalog')
        template=dataclasses.replace(self.material('managed-keys',True),kind='catalog',schema_sha256=material.schema_sha256)
        with self.assertRaises(codec.Refused):codec.reconstruct(material,self.parent,template,self.deadline)

    def test_material_repr_does_not_disclose_private_rows(self):
        sentinel='private-codec-sentinel-not-a-real-secret'
        db=sqlite3.connect(self.source/'control.sqlite')
        db.execute('UPDATE organizations SET name=?',(sentinel,));db.commit();db.close()
        material=self.material('catalog')
        self.assertIn(sentinel.encode(),material.payload)
        representation=repr(material)
        self.assertNotIn(sentinel,representation)
        self.assertNotIn('CREATE TABLE',representation)
        self.assertNotIn('payload=',representation)

    def test_invalid_decode_deadlines_refused_before_sqlite(self):
        payload=self.material('catalog').payload
        for deadline in [float('nan'),float('inf'),float('-inf'),True,'10',None,10**1000]:
            with self.subTest(deadline_type=type(deadline).__name__):
                with patch.object(codec.sqlite3,'connect',side_effect=AssertionError('SQLite opened before deadline validation')) as connect:
                    with self.assertRaises(codec.Refused):codec.decode('catalog',payload,deadline,max_bytes=len(payload))
                    connect.assert_not_called()

    def test_invalid_capture_deadlines_refused_before_descriptor_read(self):
        fd=os.open(self.source/'control.sqlite',os.O_RDONLY)
        try:
            for deadline in [float('nan'),float('inf'),float('-inf'),True,'10',None,10**1000]:
                with self.subTest(deadline_type=type(deadline).__name__):
                    with patch.object(codec.os,'dup',side_effect=AssertionError('Descriptor duplicated before deadline validation')) as duplicate,patch.object(codec.os,'pread') as pread:
                        with self.assertRaises(codec.Refused):codec.capture('catalog',fd,deadline,max_bytes=4*1024*1024)
                        duplicate.assert_not_called();pread.assert_not_called()
        finally:os.close(fd)

    def test_invalid_reconstruct_deadlines_refused_before_target_write(self):
        material=self.material('catalog');template=self.material('catalog',True)
        for deadline in [float('nan'),float('inf'),float('-inf'),True,'10',None,10**1000]:
            with self.subTest(deadline_type=type(deadline).__name__):
                with patch.object(codec.os,'dup',side_effect=AssertionError('Parent duplicated before deadline validation')) as duplicate,patch.object(codec.os,'open') as opened:
                    with self.assertRaises(codec.Refused):codec.reconstruct(material,self.parent,template,deadline)
                    duplicate.assert_not_called();opened.assert_not_called()
        self.assertEqual(list(self.target.iterdir()),[])


if __name__=='__main__':unittest.main()
