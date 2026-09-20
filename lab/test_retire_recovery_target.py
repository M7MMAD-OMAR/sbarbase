"""Guards for retiring an interrupted recovery target."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import retire_recovery_target as retire

PREFIX='sbarbase-restore-0123456789ab'


def descriptor(status='interrupted'):
    return {'prefix':PREFIX,'database':PREFIX+'-db','network':PREFIX+'-net','volume':PREFIX+'-pgdata','status':status}


def isolated(test):
    """Give a test its own state directory and a docker stub with no containers."""
    directory=tempfile.TemporaryDirectory();test.addCleanup(directory.cleanup)
    state=Path(directory.name)
    (state/'targets'/PREFIX).mkdir(parents=True)
    (state/'recovery-target.json').write_text(json.dumps(descriptor()))
    command=Mock();command.return_value.stdout=''
    paths={'record':state/'recovery-target.json','history':state/'recovery-target-history',
           'journal':state/'cutover-operation.json'}
    original={name:getattr(retire,name) for name in ('STATE',)}
    retire.STATE=state
    test.addCleanup(lambda:[setattr(retire,name,value) for name,value in original.items()])
    return state,command,paths


class RetireTests(unittest.TestCase):
    def test_a_verified_target_is_never_retired(self):
        state,command,paths=isolated(self)
        paths['record'].write_text(json.dumps(descriptor('database-restored')))
        with self.assertRaisesRegex(RuntimeError,'Refusing to retire'):
            retire.retire(command=command,**paths)
        self.assertTrue(paths['record'].exists())

    def test_running_containers_block_retirement(self):
        state,command,paths=isolated(self)
        command.return_value.stdout=PREFIX+'-db\n'+PREFIX+'-auth\n'
        with self.assertRaisesRegex(RuntimeError,'containers are running'):
            retire.retire(command=command,**paths)
        self.assertTrue(paths['record'].exists())

    def test_a_pending_hba_operation_blocks_retirement(self):
        state,command,paths=isolated(self)
        (state/'targets'/PREFIX/'hba-operation.json').write_text('{}')
        with self.assertRaisesRegex(RuntimeError,'pending per-target HBA operation'):
            retire.retire(command=command,**paths)

    def test_missing_descriptor_refuses(self):
        state,command,paths=isolated(self)
        paths['record'].unlink()
        with self.assertRaisesRegex(RuntimeError,'No active recovery target descriptor'):
            retire.retire(command=command,**paths)

    def test_malformed_identity_refuses(self):
        state,command,paths=isolated(self)
        paths['record'].write_text(json.dumps({**descriptor(),'database':'other-db'}))
        with self.assertRaisesRegex(RuntimeError,'placement mismatch'):
            retire.retire(command=command,**paths)

    def test_an_interrupted_target_is_archived_and_the_active_descriptor_removed(self):
        state,command,paths=isolated(self)
        result=retire.retire(command=command,reason='restore failed before bootstrap',**paths)
        self.assertFalse(paths['record'].exists())
        archived=json.loads(Path(result['retired']).read_text())
        self.assertEqual(archived['retired_previous_status'],'interrupted')
        self.assertEqual(archived['status'],'interrupted')
        self.assertEqual(archived['retired_reason'],'restore failed before bootstrap')
        self.assertEqual(archived['prefix'],PREFIX)
        journal=json.loads(paths['journal'].read_text())
        self.assertEqual(journal['retired_target_descriptors'][0]['prefix'],PREFIX)
        self.assertEqual(journal['retired_target_descriptors'][0]['path'],result['retired'])
        self.assertTrue(result['data_retained'])
        self.assertTrue((state/'targets'/PREFIX).exists())

    def test_repeated_retirement_appends_distinct_history_entries(self):
        state,command,paths=isolated(self)
        first=retire.retire(command=command,now=__import__('datetime').datetime(2026,9,20,15,0,0),**paths)
        paths['record'].write_text(json.dumps(descriptor('failed')))
        second=retire.retire(command=command,now=__import__('datetime').datetime(2026,9,20,16,0,0),**paths)
        self.assertNotEqual(first['retired'],second['retired'])
        journal=json.loads(paths['journal'].read_text())
        self.assertEqual(len(journal['retired_target_descriptors']),2)

    def test_retirement_holds_the_operation_lock_in_main(self):
        source=Path(retire.__file__).read_text()
        self.assertIn("(STATE/'operation.lock').open('a')",source)
        self.assertIn('fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)',source)


if __name__=='__main__':unittest.main()