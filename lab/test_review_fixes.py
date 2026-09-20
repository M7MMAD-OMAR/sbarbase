"""Adversarial-review fixes: pin reuse, installer guards, read-only verification."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import stat
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch
import uuid
import hba_adoption as adoption
import hba_generation
import hba_target
import install_server
import verify_retained


def target_fixture(cid='a'*64):
    return hba_target.Target(cid,'fixture-db','fixture','sha256:'+'c'*64)


def inspect_info(target,running=False):
    return {'Id':target.container_id,'Name':'/'+target.name,'Image':target.image,
            'Config':{'Labels':{'io.sbarbase.owner':target.owner}},
            'State':{'Running':running},
            'Mounts':[{'Type':'volume','Name':'fixture-data','Source':'/var/lib/docker/volumes/fixture-data',
                       'Destination':adoption.PGDATA,'Mode':'z'}]}


class PinReuseTests(unittest.TestCase):
    """Finding 4: a failed first run must not dead-end the retained database."""

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.state=Path(self.temp.name);self.target=target_fixture()
        self.docker=Mock()

    def publish(self):
        self.docker.return_value=SimpleNamespace(stdout=json.dumps([inspect_info(self.target)]))
        return adoption.publish_intent(self.docker,self.state,name=self.target.name,owner=self.target.owner,image=self.target.image)

    def test_existing_pin_for_the_same_container_is_bound_not_replaced(self):
        pinned=str(uuid.uuid4())
        hba_generation.publish(self.state,self.target,pinned)
        record=self.publish()
        self.assertEqual(record['generation'],pinned)
        self.assertEqual(hba_generation.load(self.state)['generation'],pinned)

    def test_existing_pin_for_another_container_refuses(self):
        other=hba_target.Target('b'*64,'fixture-db','fixture','sha256:'+'c'*64)
        hba_generation.publish(self.state,other,str(uuid.uuid4()))
        with self.assertRaisesRegex(RuntimeError,'different target container'):
            self.publish()

    def test_without_a_pin_a_fresh_generation_is_minted(self):
        record=self.publish()
        self.assertNotEqual(record['generation'],record['adoption'])


class PreservationTests(unittest.TestCase):
    """Finding on preservation method: only the first published marker is removed."""

    def test_first_marker_removed_and_older_markers_kept(self):
        published=verify_retained.MARKER+' 11111111-1111-1111-1111-111111111111\n'+verify_retained.MARKER+' 00000000-0000-0000-0000-000000000000\nlocal all all trust\n'
        preserved=verify_retained.preserved_bytes(published)
        self.assertTrue(preserved.startswith(verify_retained.MARKER+' 0000'))
        self.assertEqual(preserved.count(verify_retained.MARKER),1)

    def test_file_without_a_marker_is_returned_unchanged(self):
        text='local all all trust\n'
        self.assertEqual(verify_retained.preserved_bytes(text),text)


class InstallerGuardTests(unittest.TestCase):
    """Findings 1, 2, 9 and 11."""

    def test_pinned_images_walks_nested_lock_entries_with_pullable_references(self):
        entries=install_server.pinned_images()
        labels={label for label,_,_ in entries}
        self.assertIn('images.lock.json:db',labels)
        self.assertIn('distro-image.lock.json:default',labels)
        for label,digest,reference in entries:
            with self.subTest(label=label):
                self.assertTrue(digest.startswith('sha256:'))
                self.assertIn('@sha256:',reference)

    def test_smoke_fails_when_the_console_is_not_running(self):
        with patch.object(install_server,'console_status',return_value=(False,'server.json missing (console not started)')), \
             patch.object(install_server,'STATE',Path(tempfile.mkdtemp())):
            self.assertFalse(install_server.smoke())

    def test_console_status_requires_a_live_pid(self):
        with patch.object(install_server,'STATE',Path(tempfile.mkdtemp())):
            alive,detail=install_server.console_status()
            self.assertFalse(alive)
            self.assertIn('missing',detail)
            (install_server.STATE/'server.json').write_text(json.dumps({'pid':999999999}))
            alive,detail=install_server.console_status()
            self.assertFalse(alive)

    def test_bootstrap_file_must_be_private_and_owned(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'operator.json'
            path.write_text('{}')
            path.chmod(0o644)
            with self.assertRaises(SystemExit):install_server.bootstrap_payload(path)
            path.chmod(0o600)
            self.assertEqual(install_server.bootstrap_payload(path),'{}')
            link=Path(directory)/'link.json'
            link.symlink_to(path)
            with self.assertRaises(SystemExit):install_server.bootstrap_payload(link)

    def test_install_takes_the_operation_lock_and_refuses_a_second_operation(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(install_server,'STATE',Path(directory)):
                first=install_server.operation_lock()
                try:
                    with self.assertRaises(SystemExit):install_server.operation_lock()
                finally:first.close()
                second=install_server.operation_lock();second.close()


class TargetStatePermissionTests(unittest.TestCase):
    def test_a_new_installation_root_is_created_private(self):
        import hba_runtime
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'fresh'
            old=os.umask(0o022)
            try:hba_runtime.prepare_target_state(root,'sbarbase-restore-0123456789ab')
            finally:os.umask(old)
            self.assertEqual(stat.S_IMODE(root.stat().st_mode),0o700)


if __name__=='__main__':unittest.main()