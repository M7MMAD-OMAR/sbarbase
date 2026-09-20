"""Durable one-time host generation binding refuses silent reinitialization."""
from dataclasses import replace
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
import uuid
import hba_authority as authority
import hba_generation as generation
import hba_target


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.state=Path(self.temp.name)
        self.target=hba_target.Target('a'*64,'fixture-db','fixture','sha256:'+'b'*64)
        self.stamp=str(uuid.uuid4())

    def read_backend(self,docker,cid,stamp):
        return authority.Snapshot(cid,stamp,authority.encode({'version':1,'generation':stamp,'revision':str(uuid.uuid4()),'operations':{}}))

    def test_pin_is_durable_before_backend_initialization(self):
        events=[];sync=os.fsync
        def durable(fd):events.append('sync');sync(fd)
        def init(docker,cid,stamp):
            self.assertEqual(events,['sync','sync'])
            self.assertEqual(generation.load(self.state)['generation'],stamp)
            self.assertEqual((self.state/generation.NAME).stat().st_mode&0o777,0o600)
        with patch.object(hba_target,'observed',return_value=self.target.container_id),patch.object(authority,'initialize',side_effect=init),patch.object(authority,'read',side_effect=self.read_backend),patch.object(os,'fsync',side_effect=durable):
            snapshot=generation.initialize(Mock(),self.state,target=self.target)
        self.assertEqual(snapshot.generation,generation.load(self.state)['generation'])

    def test_each_sync_failure_keeps_pin_and_blocks_backend_dispatch(self):
        for index in (0,1):
            path=self.state/str(index);path.mkdir()
            failures=[None,None];failures[index]=OSError('sync failure')
            with patch.object(hba_target,'observed',return_value=self.target.container_id),patch.object(authority,'initialize') as initialize,patch.object(os,'fsync',side_effect=failures):
                with self.assertRaises(OSError):generation.initialize(Mock(),path,target=self.target)
                initialize.assert_not_called()
            self.assertTrue((path/generation.NAME).exists())
            with self.assertRaises(FileExistsError):generation.publish(path,self.target,str(uuid.uuid4()))

    def test_lost_init_ack_retains_pin_and_read_only_reconciliation(self):
        with patch.object(hba_target,'observed',return_value=self.target.container_id),patch.object(authority,'initialize',side_effect=RuntimeError('lost')) as initialize:
            with self.assertRaisesRegex(RuntimeError,'lost'):generation.initialize(Mock(),self.state,target=self.target)
            before=(self.state/generation.NAME).read_bytes()
            with self.assertRaises(FileExistsError):generation.initialize(Mock(),self.state,target=self.target)
            self.assertEqual(initialize.call_count,1)
            with patch.object(authority,'read',side_effect=self.read_backend):
                generation.read_existing(Mock(),self.state,target=self.target)
            self.assertEqual((self.state/generation.NAME).read_bytes(),before)

    def test_missing_backend_is_never_initialized_by_read(self):
        generation.publish(self.state,self.target,self.stamp)
        with patch.object(hba_target,'observed',return_value=self.target.container_id),patch.object(authority,'read',side_effect=RuntimeError('missing registry')),patch.object(authority,'initialize') as initialize:
            with self.assertRaisesRegex(RuntimeError,'missing'):generation.read_existing(Mock(),self.state,target=self.target)
            initialize.assert_not_called()
        self.assertTrue((self.state/generation.NAME).exists())

    def test_replacement_or_different_policy_refused_before_backend_read(self):
        generation.publish(self.state,self.target,self.stamp)
        for change in ({'container_id':'c'*64},{'image':'sha256:'+'d'*64},{'owner':'other'},{'name':'other'}):
            with patch.object(authority,'read') as read:
                with self.assertRaisesRegex(RuntimeError,'changed'):generation.read_existing(Mock(),self.state,target=replace(self.target,**change))
                read.assert_not_called()
        with self.assertRaisesRegex(RuntimeError,'changed'):generation.require(self.state,self.target,str(uuid.uuid4()))

    def test_malformed_public_and_symlink_pins_fail_closed(self):
        generation.publish(self.state,self.target,self.stamp)
        path=self.state/generation.NAME;original=path.read_text()
        for text in ('{',original.replace('"version":1','"version":1,"version":1'),original.replace('"version":1','"version":2')):
            path.write_text(text)
            with self.assertRaises((ValueError,RuntimeError)):generation.load(self.state)
        path.write_text(original);path.chmod(0o644)
        with self.assertRaises(ValueError):generation.load(self.state)
        path.unlink();path.symlink_to(self.state/'missing')
        with self.assertRaises(OSError):generation.load(self.state)


if __name__=='__main__':unittest.main()
