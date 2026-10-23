"""A planned upgrade restart leaves the owned containers running for the next start to adopt, and
every other path stops them as before (lab/kept_runtime.py)."""
import contextlib
import io
import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import console_build_check
import dev
import durable_runtime as runtime
import install_server
import kept_runtime
import leftover_runtime
import updates
import upgrade
from test_upgrade_drain import DrainFixture, at, ended

COMMIT = 'c' * 40
E = 'e_' + 'a' * 24
PINS = f'{E}:search_path=pg_catalog,{E}:statement_timeout=8s,{E}:transaction_timeout=12s'
REST_PASSWORD = 'f' * 64


def containers(**extra):
    found = {'id-db': {'image': 'sha256:db', 'name': 'sbarbase-durable-db', 'memory': 1 << 30},
             'id-rest': {'image': 'sha256:rest', 'name': f'sbarbase-durable-{E}-rest', 'memory': 1 << 28}}
    found.update(extra)
    return found


class Folder(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name) / 'upstream'
        self.state.mkdir()

    def leave(self, found=None, to=COMMIT, now=None):
        return kept_runtime.leave(found or containers(), to, self.state, now=now)


class RecordTests(Folder):
    def test_the_record_is_private_and_complete_and_names_every_container_with_its_image(self):
        self.leave()
        path = self.state / kept_runtime.NAME
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        record = kept_runtime.read(self.state)
        self.assertEqual(record['to'], COMMIT)
        self.assertEqual(record['containers']['id-db'], {'image': 'sha256:db', 'name': 'sbarbase-durable-db'})
        self.assertFalse((self.state / (kept_runtime.NAME + '.pending')).exists())

    def test_a_record_that_holds_lets_the_running_containers_be_adopted(self):
        record = self.leave(now=1000)
        self.assertIsNone(kept_runtime.judge(record, containers(), COMMIT, [], now=1060))
        # A kept container that stopped meanwhile is started by the start, as any retained one.
        self.assertIsNone(kept_runtime.judge(record, {'id-db': containers()['id-db']}, COMMIT, [], now=1060))

    def test_every_doubt_refuses_the_record(self):
        record = self.leave(now=1000)
        cases = {
            'another checkout': (containers(), 'd' * 40, [], 1060),
            'unreadable checkout': (containers(), None, [], 1060),
            'too old': (containers(), COMMIT, [], 1000 + kept_runtime.MAX_AGE + 1),
            'from the future': (containers(), COMMIT, [], 999),
            'authority to reconcile': (containers(), COMMIT, ['worker-effect.json'], 1060),
            'docker not asked': (None, COMMIT, [], 1060),
            'a container it did not keep': (containers(**{'id-new': {'image': 'sha256:x', 'name': 'sbarbase-durable-x', 'memory': 0}}), COMMIT, [], 1060),
            'another image': ({'id-db': {'image': 'sha256:other', 'name': 'sbarbase-durable-db', 'memory': 0}}, COMMIT, [], 1060),
            'another name': ({'id-db': {'image': 'sha256:db', 'name': 'renamed', 'memory': 0}}, COMMIT, [], 1060),
        }
        for label, (found, commit, waiting, now) in cases.items():
            with self.subTest(label):
                self.assertIsNotNone(kept_runtime.judge(record, found, commit, waiting, now=now))
        self.assertIsNotNone(kept_runtime.judge(None, containers(), COMMIT, [], now=1060))

    def test_a_torn_or_foreign_record_is_not_read(self):
        path = self.state / kept_runtime.NAME
        for text in ('{', '[]', json.dumps({'version': 2, 'to': COMMIT, 'at': 1, 'containers': {}}),
                     json.dumps({'version': 1, 'to': COMMIT, 'at': '1', 'containers': {}})):
            with self.subTest(text):
                path.write_text(text)
                self.assertIsNone(kept_runtime.read(self.state))

    def test_no_record_asks_neither_docker_nor_git(self):
        with patch.object(kept_runtime, 'running', side_effect=AssertionError('docker')), \
                patch.object(kept_runtime, 'head', side_effect=AssertionError('git')):
            self.assertEqual(kept_runtime.current(self.state), (None, 'no record'))

    def test_current_reads_pending_authority_from_the_state(self):
        self.leave()
        (self.state / 'hba-operation.json').write_text('{}')
        with patch.object(kept_runtime, 'running', return_value=containers()), patch.object(kept_runtime, 'head', return_value=COMMIT):
            record, reason = kept_runtime.current(self.state)
        self.assertIsNone(record)
        self.assertIn('hba-operation.json', reason)

    def test_only_a_version_that_ships_the_module_is_left_a_record(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertFalse(kept_runtime.supported(root))
            (Path(root) / 'lab').mkdir()
            (Path(root) / 'lab' / 'kept_runtime.py').write_text('')
            self.assertTrue(kept_runtime.supported(root))

    def test_the_memory_credit_is_their_anonymous_and_shared_memory_and_nothing_when_unread(self):
        cgroup = self.state / 'cgroup'
        def account(cid, text, pattern=kept_runtime.CGROUP_PATHS[0]):
            folder = cgroup / pattern.format(id=cid)
            folder.mkdir(parents=True)
            (folder / 'memory.stat').write_text(text)
        account('id-db', f'anon {300 << 20}\nfile {900 << 20}\nshmem {128 << 20}\n')
        account('id-rest', f'anon {64 << 20}\nshmem 0\nfile {10 << 20}\n', kept_runtime.CGROUP_PATHS[1])
        # The file cache MemAvailable already counts is left out.
        self.assertEqual(kept_runtime.credit_mib(['id-db', 'id-rest'], cgroup), 300 + 128 + 64)
        # A container without accounting (it exited) counts nothing; one that reads oddly makes it all 0.
        self.assertEqual(kept_runtime.credit_mib(['id-db', 'id-gone'], cgroup), 300 + 128)
        account('id-odd', 'file 1\n')
        self.assertEqual(kept_runtime.credit_mib(['id-db', 'id-odd'], cgroup), 0)
        self.assertEqual(kept_runtime.credit_mib([], cgroup), 0)

    def test_a_claimed_record_serves_only_the_start_that_took_it_and_its_stages(self):
        self.leave(now=1000)
        record = kept_runtime.claim(self.state)
        self.assertEqual(record['claimed']['pid'], os.getpid())
        self.assertIsNone(kept_runtime.judge(record, containers(), COMMIT, [], now=1060))
        me = os.getpid()
        # A stage of that start, its child, may use it too.
        with patch.object(kept_runtime.os, 'getpid', return_value=1), patch.object(kept_runtime.os, 'getppid', return_value=me):
            self.assertIsNone(kept_runtime.judge(record, containers(), COMMIT, [], now=1060))
        # A start that died while it adopted them: the next one stops what it left.
        dead = dict(record, claimed={'pid': 2 ** 22 + 7, 'started': 5})
        self.assertIn('earlier start', kept_runtime.judge(dead, containers(), COMMIT, [], now=1060))
        reused = dict(record, claimed={'pid': os.getpid(), 'started': record['claimed']['started'] + 1})
        self.assertIn('earlier start', kept_runtime.judge(reused, containers(), COMMIT, [], now=1060))
        # A live process that is neither the one that took it nor its parent may not use it either.
        unknown = dict(record, claimed={'pid': os.getpid(), 'started': None})
        self.assertIn('earlier start', kept_runtime.judge(unknown, containers(), COMMIT, [], now=1060))
        other = dict(record, claimed={'pid': 1, 'started': kept_runtime.started(1)})
        self.assertIsNotNone(other['claimed']['started'])
        self.assertIn('earlier start', kept_runtime.judge(other, containers(), COMMIT, [], now=1060))

    def test_only_a_full_commit_id_counts_as_the_checkout(self):
        for output, expected in (('c' * 40 + '\n', 'c' * 40), ('c' * 64 + '\n', 'c' * 64), ('c' * 12 + '\n', None),
                                 ('c' * 41 + '\n', None), ('C' * 40 + '\n', None), ('', None)):
            with patch.object(kept_runtime.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=output)):
                self.assertEqual(kept_runtime.head(), expected)

    def test_the_record_names_the_commit_the_containers_ran_with(self):
        record = kept_runtime.leave(containers(), COMMIT, self.state, origin='b' * 40)
        self.assertEqual(kept_runtime.read(self.state)['from'], 'b' * 40)

    def test_studio_is_not_credited_since_the_placement_does_not_count_it(self):
        cgroup = self.state / 'cgroup'
        for cid in ('id-db', 'id-studio'):
            folder = cgroup / kept_runtime.CGROUP_PATHS[0].format(id=cid)
            folder.mkdir(parents=True)
            (folder / 'memory.stat').write_text(f'anon {100 << 20}\nshmem 0\n')
        folder = cgroup / kept_runtime.CGROUP_PATHS[0].format(id='id-gone-kind')
        folder.mkdir(parents=True)
        (folder / 'memory.stat').write_text(f'anon {100 << 20}\nshmem 0\n')
        kept = {'id-db': {'name': 'sbarbase-durable-db'}, 'id-studio': {'name': f'sbarbase-durable-{E}-studio'}, 'id-meta': {'name': f'sbarbase-durable-{E}-meta'},
                'id-gone-kind': {'name': 'sbarbase-durable-retired-service'}}
        # Only what the start placement counts is credited: not Studio, not a kind this version no longer runs.
        self.assertEqual(kept_runtime.credit_mib(kept, cgroup), 100)

    def test_a_check_after_the_settle_accepts_a_record_that_just_turned_too_old(self):
        record = self.leave(now=1000)
        late = 1000 + kept_runtime.MAX_AGE + 10
        self.assertIsNotNone(kept_runtime.judge(record, containers(), COMMIT, [], now=late))
        self.assertIsNone(kept_runtime.judge(record, containers(), COMMIT, [], now=late, grace=kept_runtime.LATER_GRACE))


class LeftoverTests(Folder):
    def settle(self, record=(None, 'no record'), **options):
        stops, discarded = [], []
        answers = iter([['c1'], []])
        outcome = leftover_runtime.settle(self.state, running=lambda: next(answers), stop=lambda: stops.append(1) or 0,
                                          say=lambda *a, **k: None, kept=lambda state: record,
                                          discard=lambda state: discarded.append(1), **options)
        return outcome, stops, discarded

    def test_a_record_that_holds_keeps_the_runtime(self):
        outcome, stops, discarded = self.settle(({'containers': {}}, None))
        self.assertEqual(outcome, ('kept', None))
        self.assertEqual((stops, discarded), ([], []))

    def test_a_record_that_does_not_hold_is_discarded_and_the_runtime_stopped(self):
        outcome, stops, discarded = self.settle((None, 'the record is too old'))
        self.assertEqual(outcome[0], 'stopped')
        self.assertEqual((stops, discarded), ([1], [1]))

    def test_without_a_record_the_runtime_is_stopped_as_before(self):
        outcome, stops, discarded = self.settle()
        self.assertEqual(outcome[0], 'stopped')
        self.assertEqual((stops, discarded), ([1], []))

    def test_a_record_with_nothing_running_is_discarded(self):
        discarded = []
        outcome = leftover_runtime.settle(self.state, running=lambda: [], stop=lambda: 0, say=lambda *a, **k: None,
                                          kept=lambda state: ({'containers': {}}, None), discard=lambda state: discarded.append(1))
        self.assertEqual((outcome, discarded), (('nothing', None), [1]))

    def test_a_live_owner_or_pending_authority_still_declines_before_any_record_is_read(self):
        def never(state):
            raise AssertionError('record read')
        (self.state / 'worker-effect.json').write_text('{}')
        outcome = leftover_runtime.settle(self.state, running=lambda: ['c1'], stop=lambda: 0, say=lambda *a, **k: None,
                                          kept=never, discard=never)
        self.assertEqual(outcome[0], 'decline')


class PreflightTests(Folder):
    def test_running_containers_block_unless_a_planned_restart_left_them(self):
        def docker(*args, **kwargs):
            return SimpleNamespace(stdout='abc\n' if args[0] == 'ps' and '-q' in args else '', returncode=0)
        with patch.object(install_server, 'docker', side_effect=docker), patch.object(install_server, 'run', return_value=SimpleNamespace(returncode=0)), \
                patch.object(install_server, 'STATE', self.state):
            with patch.object(install_server, 'kept_record', return_value=(None, 'no record')):
                self.assertIn(('blocker', 'Owned containers are already running; stop or supervise them instead of installing'), install_server.state())
            with patch.object(install_server, 'kept_record', return_value=({'containers': {}}, None)):
                findings = install_server.state()
        self.assertFalse(any(kind == 'blocker' and 'already running' in text for kind, text in findings))
        self.assertIn('planned upgrade restart', ' '.join(text for _, text in findings))

    def test_the_headroom_counts_back_what_kept_containers_hold(self):
        meminfo = 'MemAvailable:    1048576 kB\n'
        with patch.object(install_server, 'Path', side_effect=lambda p: SimpleNamespace(read_text=lambda: meminfo) if p == '/proc/meminfo' else Path(p)), \
                patch.object(install_server, 'planned_placement', return_value=(1500, 1, 'placement')), \
                patch.object(install_server, 'headroom_requirement', return_value=(1500, 'composition')), \
                patch.object(install_server, 'STATE', self.state):
            with patch.object(install_server, 'kept_record', return_value=(None, 'no record')):
                self.assertTrue(any('Host headroom insufficient' in text for _, text in install_server.capacity()))
            with patch.object(install_server, 'kept_record', return_value=({'containers': {'a': {}}}, None)), \
                    patch.object(install_server, 'kept_credit_mib', return_value=600):
                self.assertFalse(any('Host headroom insufficient' in text for _, text in install_server.capacity()))


class CloseTests(Folder):
    def close(self, owned, keep, leave=True):
        stages = []
        with patch.object(dev, 'STATE', self.state), patch.object(dev, 'leave_runtime', return_value=leave), \
                patch.object(dev, 'run_stage', side_effect=lambda command, *a, **k: stages.append((command, k)) or 0), \
                patch.object(dev, 'notify_installation'), contextlib.redirect_stdout(io.StringIO()):
            (self.state / kept_runtime.NAME).write_text('{}')
            dev.close_runtime(owned, keep, 'b' * 40)
        return stages

    def test_a_completed_upgrade_exit_leaves_the_runtime_running(self):
        self.assertEqual(self.close(True, True), [])
        self.assertTrue((self.state / kept_runtime.NAME).exists())

    def test_every_other_way_out_of_a_start_that_owns_the_runtime_stops_it(self):
        for keep, leave in ((False, True), (True, False)):
            with self.subTest(keep=keep, leave=leave):
                stages = self.close(True, keep, leave)
                self.assertEqual(stages[0][0], ['/usr/bin/python3', 'lab/installation_runtime.py', 'stop'])
                self.assertEqual(stages[0][1]['timeout'], dev.STOP_STAGE_SECONDS)
                self.assertFalse((self.state / kept_runtime.NAME).exists())

    def test_a_start_that_owns_nothing_stops_nothing(self):
        self.assertEqual(self.close(False, False), [])


class MainTests(Folder):
    """dev.main itself, with the host, Docker and the stages stood in for."""

    def main(self, prepare=lambda: False, stage=lambda *a, **k: 0, restart=False, settled='kept', keep=None, claim=None, signalled=False):
        record = {'closed': [], 'claimed': [], 'discarded': [], 'adopted': []}
        handlers = {}

        class Supervisor:
            def __init__(self, *args, **kwargs):
                self.restart_for_upgrade = restart
                self.keep_runtime = restart if keep is None else keep

            def run(self):
                if signalled:
                    # The service is stopped while the supervisor tears down after a completed move.
                    handlers[dev.signal.SIGTERM]()
        patches = [patch.object(dev, 'STATE', self.state), patch.object(dev.docker_profile, 'require_supported'),
                   patch.object(dev.os, 'chdir'), patch.object(dev.signal, 'signal', side_effect=lambda number, handler: handlers.__setitem__(number, handler)),
                   patch.object(dev, 'settle_leftover', return_value=settled),
                   patch.object(dev.kept_runtime, 'claim', side_effect=claim or (lambda state: record['claimed'].append(state))),
                   patch.object(dev.kept_runtime, 'head', return_value='b' * 40),
                   patch.object(dev.kept_runtime, 'discard', side_effect=lambda state: record['discarded'].append(state)),
                   patch.object(dev, 'close_runtime', side_effect=lambda *a: record['closed'].append(a)),
                   patch.object(dev, 'upgrade_prepare', side_effect=prepare), patch.object(dev, 'run_stage', side_effect=stage),
                   patch.object(dev.console_build_check, 'adopt_staged', side_effect=lambda: record['adopted'].append(1) or False),
                   patch.object(dev.console_build_check, 'is_fresh', return_value=(True, 'fresh')),
                   patch.object(dev, 'notify_installation'), patch.object(dev, 'upgrade_notices'),
                   patch.object(dev, 'upgrade_outcome', return_value=False), patch.object(dev, 'Supervisor', Supervisor)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        import run
        with patch.object(run, 'private_dir'), patch.object(dev.sys, 'argv', ['lab/dev.py']), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                dev.main()
                code = None
            except SystemExit as exit:
                code = exit.code
        return code, record

    def test_a_start_that_adopted_kept_containers_and_fails_before_its_runtime_is_up_stops_them(self):
        def broken():
            raise RuntimeError('the settle stage failed')
        code, record = self.main(prepare=broken)
        self.assertEqual(code, 1)
        self.assertEqual(record['claimed'], [self.state])
        self.assertEqual(record['closed'], [(True, False, 'b' * 40)])

    def test_a_cancelled_start_that_adopted_them_stops_them_too(self):
        def cancelled(*args, **kwargs):
            raise InterruptedError('Local installation startup cancelled')
        code, record = self.main(stage=cancelled)
        self.assertEqual(record['closed'], [(True, False, 'b' * 40)])

    def test_a_start_whose_runtime_is_up_discards_the_record_and_uses_the_staged_console(self):
        code, record = self.main()
        self.assertIsNone(code)
        self.assertEqual(record['adopted'], [1])
        self.assertEqual(record['discarded'], [self.state])
        self.assertEqual(record['closed'], [(True, False, 'b' * 40)])

    def test_a_start_that_did_not_adopt_discards_any_record_before_anything_else(self):
        def broken():
            raise RuntimeError('the settle stage failed')
        for settled in ('nothing', 'unknown', 'decline'):
            code, record = self.main(prepare=broken, settled=settled)
            self.assertEqual(record['discarded'], [self.state], settled)

    def test_a_claim_that_cannot_be_written_still_stops_what_this_start_holds(self):
        def full(state):
            raise OSError(28, 'No space left on device')
        code, record = self.main(settled='kept', claim=full)
        self.assertEqual(code, 1)
        self.assertEqual(record['closed'], [(True, False, None)])

    def test_a_stop_asked_for_after_a_completed_move_stops_the_runtime(self):
        code, record = self.main(restart=True, signalled=True)
        self.assertEqual(record['closed'], [(True, False, 'b' * 40)])

    def test_keeping_needs_a_restart_for_an_upgrade(self):
        code, record = self.main(restart=False, keep=True)
        self.assertEqual(record['closed'], [(True, False, 'b' * 40)])

    def test_a_start_that_did_not_adopt_discards_any_record(self):
        code, record = self.main(settled='nothing')
        self.assertEqual(record['claimed'], [])
        self.assertEqual(record['discarded'][:1], [self.state])
        self.assertEqual(record['closed'], [(True, False, 'b' * 40)])

    def test_a_completed_upgrade_exit_asks_to_keep_the_runtime(self):
        code, record = self.main(restart=True)
        self.assertEqual(code, dev.RESTART_FOR_UPGRADE)
        self.assertEqual(record['closed'], [(True, True, 'b' * 40)])


class StagingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)

    def test_installing_a_version_also_builds_its_console_ahead_both_ways(self):
        called = []
        with patch.object(upgrade, 'git', return_value=''), patch.object(upgrade, 'set_aside_evidence', return_value=[]), \
                patch.object(upgrade, 'owner_of_checkout'), patch.object(upgrade.lab, 'atomic'), \
                patch.object(upgrade, 'install_dependencies', side_effect=lambda: called.append('install')), \
                patch.object(upgrade, 'INTENT', self.folder / 'intent.json'):
            upgrade.checkout('c' * 40, {})
            with patch.object(upgrade.upgrade_guard, 'move_checkout', side_effect=lambda layout, commit, intent, install, aside: install(None)), \
                    patch.object(upgrade.upgrade_guard, 'aside_folder'), patch.object(upgrade, 'layout'):
                upgrade.move_back('c' * 40, {}, {})
        self.assertEqual(called, ['install', 'install'])
        with patch.object(upgrade.subprocess, 'run', side_effect=lambda *a, **k: called.append('bun') or SimpleNamespace(returncode=0)), \
                patch.object(upgrade, 'stage_console', side_effect=lambda: called.append('stage')):
            upgrade.install_dependencies()
        self.assertEqual(called[2:], ['bun', 'stage'])

    def test_only_a_version_that_puts_a_staged_build_in_place_gets_one(self):
        root = self.folder / 'checkout'
        (root / 'lab').mkdir(parents=True)
        for text, built in (('def is_fresh(): pass\n', False), ('def adopt_staged(out): pass\n', True)):
            (root / 'lab' / 'console_build_check.py').write_text(text)
            with patch.object(upgrade, 'ROOT', root), patch.object(console_build_check, 'stage', return_value=True) as stage, \
                    patch.object(console_build_check, 'is_fresh', return_value=(False, 'stale')):
                upgrade.stage_console()
            self.assertEqual(stage.called, built)

    def test_no_build_ahead_when_the_served_page_already_fits_the_version(self):
        with patch.object(upgrade, 'ROOT', runtime.lab.ROOT), patch.object(console_build_check, 'is_fresh', return_value=(True, 'fresh')), \
                patch.object(console_build_check, 'stage', side_effect=AssertionError('built')):
            upgrade.stage_console()

    def test_no_build_ahead_on_a_host_short_of_memory(self):
        folder = self.folder
        (folder / 'meminfo').write_text('MemAvailable:     524288 kB\n')
        with patch.object(console_build_check, 'build', side_effect=AssertionError('built')):
            self.assertFalse(console_build_check.stage(meminfo=folder / 'meminfo'))

    def test_a_console_that_cannot_be_built_ahead_is_left_to_the_start(self):
        for failure in (OSError('no bun'), upgrade.subprocess.TimeoutExpired('bun', 900)):
            with patch.object(console_build_check, 'stage', side_effect=failure), contextlib.redirect_stdout(io.StringIO()) as out, \
                    patch.object(upgrade, 'ROOT', runtime.lab.ROOT), patch.object(console_build_check, 'is_fresh', return_value=(False, 'stale')):
                upgrade.stage_console()
            self.assertIn('the start builds it', out.getvalue())

    def test_a_build_that_ran_but_left_no_usable_page_is_not_staged(self):
        folder = self.folder
        (folder / 'meminfo').write_text('MemAvailable:    8388608 kB\n')
        with patch.object(console_build_check, 'build', return_value={'exit': 0}), \
                patch.object(console_build_check, 'is_fresh', return_value=(False, 'index.html is missing')):
            self.assertFalse(console_build_check.stage(meminfo=folder / 'meminfo'))


class SupervisorTests(DrainFixture):
    def test_a_completed_upgrade_keeps_the_runtime_and_a_restart_to_settle_does_not(self):
        updates.create_request('apply', '0.2.0', moment=at(12))
        self.turn()
        ended(self.supervisor.worker)
        self.turn()
        self.turn()
        self.assertTrue(self.supervisor.restart_for_upgrade)
        self.assertTrue(self.supervisor.keep_runtime)
        other = dev.Supervisor(self.supervisor.stop_event, worker_fd=99, catalog=self.folder / 'absent.sqlite')
        with contextlib.redirect_stdout(io.StringIO()):
            other.restart_to_settle('the checkout moved')
        self.assertTrue(other.restart_for_upgrade)
        self.assertFalse(getattr(other, 'keep_runtime', False))


class LeaveTests(Folder):
    def setUp(self):
        super().setUp()
        guarded = patch.dict(dev.os.environ, {'SBARBASE_GUARDED': '1'})
        guarded.start()
        self.addCleanup(guarded.stop)

    def test_leave_records_only_for_a_version_that_knows_the_record_and_a_docker_that_answers(self):
        with patch.object(dev, 'STATE', self.state):
            with patch.object(kept_runtime, 'supported', return_value=False), patch.object(kept_runtime, 'head', return_value=COMMIT), \
                    patch.object(kept_runtime, 'running', return_value=containers()):
                self.assertFalse(dev.leave_runtime())
            with patch.object(kept_runtime, 'supported', return_value=True), patch.object(kept_runtime, 'head', return_value=COMMIT):
                for found in (None, {}):
                    with patch.object(kept_runtime, 'running', return_value=found):
                        self.assertFalse(dev.leave_runtime())
                self.assertFalse((self.state / kept_runtime.NAME).exists())
                with patch.object(kept_runtime, 'running', return_value=containers()):
                    self.assertTrue(dev.leave_runtime('b' * 40))
        self.assertEqual(kept_runtime.read(self.state)['to'], COMMIT)
        self.assertEqual(kept_runtime.read(self.state)['from'], 'b' * 40)

    def test_a_record_that_cannot_be_written_stops_the_runtime_as_before(self):
        with patch.object(dev, 'STATE', self.state), patch.object(kept_runtime, 'supported', return_value=True), \
                patch.object(kept_runtime, 'head', return_value=COMMIT), patch.object(kept_runtime, 'running', return_value=containers()), \
                patch.object(kept_runtime, 'leave', side_effect=OSError(28, 'No space left on device')), \
                patch.object(kept_runtime, 'discard') as discard, contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(dev.leave_runtime())
        discard.assert_called_once_with(self.state)

    def test_a_start_from_a_terminal_stops_the_runtime_as_before(self):
        with patch.object(dev, 'STATE', self.state), patch.object(kept_runtime, 'supported', return_value=True), \
                patch.object(kept_runtime, 'head', return_value=COMMIT), patch.object(kept_runtime, 'running', return_value=containers()), \
                patch.dict(dev.os.environ, {'SBARBASE_GUARDED': ''}):
            self.assertFalse(dev.leave_runtime())

    def test_a_moved_environment_stops_the_runtime_as_before(self):
        (self.state / 'cutover-operation.json').write_text('{}')
        with patch.object(dev, 'STATE', self.state), patch.object(kept_runtime, 'supported', return_value=True), \
                patch.object(kept_runtime, 'head', return_value=COMMIT), patch.object(kept_runtime, 'running', return_value=containers()):
            self.assertFalse(dev.leave_runtime())

    def test_an_unreadable_checkout_stops_the_runtime_as_before(self):
        with patch.object(dev, 'STATE', self.state), patch.object(kept_runtime, 'supported', return_value=True), \
                patch.object(kept_runtime, 'head', return_value=None), patch.object(kept_runtime, 'running', return_value=containers()):
            self.assertFalse(dev.leave_runtime())


class RuntimeFixture(Folder):
    def setUp(self):
        super().setUp()
        # The block device the IO limits name is the host's; a fixed one keeps the tests off it.
        device = patch.object(runtime.resource_policy, 'device', return_value='8:0')
        device.start()
        self.addCleanup(device.stop)
        self.runtime = runtime.Runtime.__new__(runtime.Runtime)
        self.runtime.values = {'environments': {E: {'rest': REST_PASSWORD}}}
        self.runtime.kept = {'containers': {'id-rest': {'image': 'sha256:rest', 'name': f'sbarbase-durable-{E}-rest'}}}
        self.runtime.sql = Mock(side_effect=self.answer)

    def answer(self, query, *args, **kwargs):
        if "coalesce(d.datname, '*')" in query:
            return SimpleNamespace(returncode=0, stdout=self.settings)
        if 'FROM pg_catalog.pg_authid' in query:
            return SimpleNamespace(returncode=0, stdout=self.login)
        return SimpleNamespace(returncode=0, stdout='t')

    def item(self, **overrides):
        item = {'Id': 'id-rest', 'Image': 'sha256:rest', 'Name': f'/sbarbase-durable-{E}-rest', 'State': {'Running': True}}
        item.update(overrides)
        return item


class ResumeTests(RuntimeFixture):
    def test_a_rest_a_planned_restart_kept_still_stops_before_the_pin(self):
        order = []
        self.runtime.rest_deadlines = Mock(side_effect=lambda *a, **k: order.append('pin'))
        self.runtime.data_api = Mock(side_effect=lambda *a, **k: order.append('data api'))
        self.runtime.activate_services = Mock(side_effect=lambda *a, **k: order.append('activate'))
        self.runtime.scheduled_jobs_back = Mock()
        (self.state / 'endpoints.json').write_text(json.dumps({E: {}}))
        with patch.object(runtime, 'STATE', self.state), patch.object(runtime.source_fence, 'is_fenced', return_value=False), \
                patch.object(runtime.effect_receipt, 'require_settled'), patch.object(runtime, 'inspect', return_value=self.item()), \
                patch.object(runtime.lab, 'docker', side_effect=lambda *a, **k: order.append(a[:2])):
            self.runtime.resume(E)
        self.assertEqual(order, [('stop', f'sbarbase-durable-{E}-rest'), 'pin', 'data api', 'activate'])

    def test_only_a_running_container_the_record_kept_unchanged_counts(self):
        self.assertTrue(self.runtime.kept_running(self.item()))
        for change in ({'Id': 'id-other'}, {'Image': 'sha256:new'}, {'Name': '/renamed'}, {'State': {'Running': False}}):
            self.assertFalse(self.runtime.kept_running(self.item(**change)))
        self.runtime.kept = None
        self.assertFalse(self.runtime.kept_running(self.item()))


class LaunchTests(RuntimeFixture):
    def test_a_running_container_that_must_be_replaced_is_stopped_before_it_is_removed(self):
        calls = []
        item = {'Id': 'id-rest', 'Image': 'sha256:old', 'State': {'Running': True}, 'Config': {'Env': []}}
        self.runtime.pins = {'rest': {'id': 'sha256:new'}}
        with patch.object(self.runtime, 'image', return_value=('ref', 'sha256:new')), patch.object(runtime, 'inspect', return_value=item), \
                patch.object(runtime, 'upgrade_allows', return_value=True), \
                patch.object(runtime.lab, 'docker', side_effect=lambda *a, **k: calls.append(a) or ((_ for _ in ()).throw(StopIteration) if a[0] == 'run' else SimpleNamespace(stdout='', returncode=0))), \
                patch.object(runtime.resource_policy, 'container_flags', return_value={'memory': None, 'cpus': None, 'label': 'production', 'pids': 256, 'shares': 512, 'weight': 400}), \
                patch.object(runtime.resource_policy, 'io_flags', return_value=[]), patch.object(runtime.lab, 'secure_file'), \
                patch.object(runtime, 'PRIVATE', self.state):
            with self.assertRaises(StopIteration):
                self.runtime.launch(f'sbarbase-durable-{E}-rest', 'rest', {}, '256m', .25)
        self.assertEqual([call[:2] for call in calls[:2]], [('stop', 'id-rest'), ('rm', '-f')])

    def test_a_container_launch_creates_is_noted_as_run_by_this_start(self):
        self.runtime.pins = {'rest': {'id': 'sha256:rest'}}
        with patch.object(self.runtime, 'image', return_value=('ref', 'sha256:rest')), patch.object(runtime, 'inspect', return_value=None), \
                patch.object(runtime.lab, 'docker', return_value=SimpleNamespace(stdout='id-new\n', returncode=0)), \
                patch.object(runtime.resource_policy, 'container_flags', return_value={'memory': None, 'cpus': None, 'label': 'production', 'pids': 256, 'shares': 512, 'weight': 400}), \
                patch.object(runtime.resource_policy, 'io_flags', return_value=[]), patch.object(runtime.lab, 'secure_file'), patch.object(runtime, 'PRIVATE', self.state):
            self.assertEqual(self.runtime.launch(f'sbarbase-durable-{E}-rest', 'rest', {}, '256m', .25), ('id-new', True))
        self.assertIn('id-new', self.runtime.touched)

    def test_a_matching_retained_container_is_started_and_noted_as_run_by_this_start(self):
        self.runtime.pins = {'rest': {'id': 'sha256:rest'}}
        item = {'Id': 'id-rest', 'Image': 'sha256:rest', 'State': {'Running': True}, 'Config': {'Env': []}, 'Mounts': [],
                'NetworkSettings': {'Networks': {runtime.NETWORK: {}}}}
        with patch.object(self.runtime, 'image', return_value=('ref', 'sha256:rest')), patch.object(runtime, 'inspect', return_value=item), \
                patch.object(runtime.lab, 'docker', return_value=SimpleNamespace(stdout='', returncode=0)):
            self.assertEqual(self.runtime.launch(f'sbarbase-durable-{E}-rest', 'rest', {}, '256m', .25), ('id-rest', False))
        self.assertIn('id-rest', self.runtime.touched)

    def test_the_database_is_created_with_the_stop_time_its_shutdown_needs(self):
        calls = []
        self.runtime.pins = {'db': {'id': 'sha256:db'}, 'rest': {'id': 'sha256:db'}}
        with patch.object(self.runtime, 'image', return_value=('ref', 'sha256:db')), patch.object(runtime, 'inspect', return_value=None), \
                patch.object(runtime.lab, 'docker', side_effect=lambda *a, **k: calls.append(a) or ((_ for _ in ()).throw(StopIteration) if a[0] == 'run' else SimpleNamespace(stdout='', returncode=0))), \
                patch.object(runtime.resource_policy, 'container_flags', return_value={'memory': None, 'cpus': None, 'label': 'system', 'pids': 256, 'shares': 512, 'weight': 400}), \
                patch.object(runtime.resource_policy, 'io_flags', return_value=[]), patch.object(runtime.lab, 'secure_file'), \
                patch.object(runtime, 'PRIVATE', self.state):
            with self.assertRaises(StopIteration):
                self.runtime.launch(runtime.DB, 'db', {}, '1024m', 1)
            with self.assertRaises(StopIteration):
                self.runtime.launch(f'sbarbase-durable-{E}-rest', 'rest', {}, '256m', .25)
        database, rest = [call for call in calls if call[0] == 'run']
        self.assertIn('--stop-timeout', database)
        self.assertEqual(database[database.index('--stop-timeout') + 1], str(runtime.STOP_DATABASE_SECONDS))
        self.assertNotIn('--stop-timeout', rest)


class FunctionsTests(RuntimeFixture):
    def setUp(self):
        super().setUp()
        self.runtime.values = {'environments': {E: {'jwt': 'j' * 64, 'rest': REST_PASSWORD}}}
        self.runtime.kept = {'from': 'b' * 40, 'containers': {'id-fn': {'image': 'sha256:fn', 'name': f'sbarbase-durable-{E}-functions'}}}
        self.runtime.pins = {'functions': {'id': 'sha256:fn'}}
        self.network = runtime.functions_network(E)
        self.mounts = [{'Source': str(source), 'Destination': target, 'RW': False} for source, target in runtime.functions_binds(E)]
        self.diff = 0
        # What the image itself sets, which a container carries besides the settings launch gives it.
        self.image_env = ['PATH=/usr/local/bin:/usr/bin', 'DENO_DIR=/deno-dir']

    def container(self, **overrides):
        key = lambda role, seconds=10 * 365 * 24 * 3600, secret='j' * 64: runtime.service_token(secret, {'role': role, 'iss': 'sbarbase'}, seconds)
        env = {'PATH': '/usr/local/bin:/usr/bin', 'DENO_DIR': '/deno-dir', 'SB_JWT_SECRET': 'j' * 64, 'SB_STORAGE_URL': 'http://172.30.4.1:25433',
               'SUPABASE_ANON_KEY': key('anon'), 'SUPABASE_SERVICE_ROLE_KEY': key('service_role')}
        env.update(overrides.pop('env', {}))
        label = overrides.pop('spec', 'same')
        spec = runtime.launch_spec('functions', 'ref', '384m', .5, 'production.functions', self.network,
                                   binds=runtime.functions_binds(E), command=runtime.FUNCTIONS_COMMAND)
        item = {'Id': 'id-fn', 'Image': 'sha256:fn', 'Name': f'/sbarbase-durable-{E}-functions', 'State': {'Running': True},
                'Config': {'Env': [f'{k}={v}' for k, v in env.items()],
                           'Labels': {} if label is None else {'io.sbarbase.spec': spec if label == 'same' else label}},
                'Mounts': self.mounts,
                'NetworkSettings': {'Networks': {self.network: {}, runtime.EGRESS: {}}}}
        item.update(overrides)
        return item

    def kept(self, item):
        wanted = {'SB_JWT_SECRET': 'j' * 64, 'SB_STORAGE_URL': 'http://172.30.4.1:25433', 'SUPABASE_ANON_KEY': 'fresh', 'SUPABASE_SERVICE_ROLE_KEY': 'fresh'}
        with patch.object(runtime, 'inspect', return_value={'Internal': True}), \
                patch.object(runtime, 'functions_address', return_value=('172.30.4.1', '172.30.4.0/24')), \
                patch.object(self.runtime, 'functions_configuration', return_value=wanted), \
                patch.object(self.runtime, 'image', return_value=('ref', 'sha256:fn')), \
                patch.object(runtime.subprocess, 'run', return_value=SimpleNamespace(returncode=self.diff)), \
                patch.object(runtime.lab, 'docker', return_value=SimpleNamespace(stdout=json.dumps(self.image_env), returncode=0)):
            return self.runtime.functions_kept(E, self.runtime.values['environments'][E], item, self.network)

    def test_an_unchanged_kept_functions_container_keeps_serving(self):
        self.assertTrue(self.kept(self.container()))

    def test_the_real_settings_are_comparable_from_one_start_to_the_next(self):
        # Their two keys carry when they were made; the rest of the settings must match exactly.
        first = self.runtime.functions_configuration(E, self.runtime.values['environments'][E], '172.30.4.1')
        time.sleep(1.1)
        second = self.runtime.functions_configuration(E, self.runtime.values['environments'][E], '172.30.4.1')
        self.assertEqual({k: v for k, v in first.items() if k not in runtime.FUNCTIONS_KEYS},
                         {k: v for k, v in second.items() if k not in runtime.FUNCTIONS_KEYS})
        for key, role in runtime.FUNCTIONS_KEYS.items():
            self.assertTrue(runtime.valid_service_token(first[key], 'j' * 64, role))

    def test_any_difference_recreates_it_as_before(self):
        key = lambda role, seconds=10 * 365 * 24 * 3600, secret='j' * 64: runtime.service_token(secret, {'role': role, 'iss': 'sbarbase'}, seconds)
        changes = {'another network': {'NetworkSettings': {'Networks': {self.network: {}, runtime.EGRESS: {}, runtime.NETWORK: {}}}},
                   'no outbound network': {'NetworkSettings': {'Networks': {self.network: {}}}},
                   'another image': {'Image': 'sha256:new'},
                   'another launch description (limits, command, mounts)': {'spec': 'other'},
                   'no launch description': {'spec': None},
                   'other settings': {'env': {'SB_STORAGE_URL': 'http://elsewhere'}},
                   'a key of another secret': {'env': {'SUPABASE_ANON_KEY': key('anon', secret='k' * 64)}},
                   'a key for another role': {'env': {'SUPABASE_ANON_KEY': key('service_role')}},
                   'a key about to expire': {'env': {'SUPABASE_SERVICE_ROLE_KEY': key('service_role', seconds=60)}},
                   'a setting this version no longer gives': {'env': {'SB_OLD_SETTING': '1'}},
                   'a key from another issuer': {'env': {'SUPABASE_ANON_KEY': runtime.service_token('j' * 64, {'role': 'anon', 'iss': 'other'}, 10 ** 9)}},
                   'a key granting more than its role': {'env': {'SUPABASE_ANON_KEY': runtime.service_token('j' * 64, {'role': 'anon', 'iss': 'sbarbase', 'aud': 'x'}, 10 ** 9)}},
                   'a writable mount': {'Mounts': [dict(self.mounts[0], RW=True), *self.mounts[1:]]},
                   'a missing mount': {'Mounts': self.mounts[:2]},
                   'not kept': {'Id': 'id-other'}}
        for label, change in changes.items():
            with self.subTest(label):
                self.assertFalse(self.kept(self.container(**change)))

    def test_the_main_service_code_is_compared_between_the_recorded_commit_and_the_checkout(self):
        calls = []
        with patch.object(runtime, 'inspect', return_value={'Internal': True}), \
                patch.object(runtime, 'functions_address', return_value=('172.30.4.1', '172.30.4.0/24')), \
                patch.object(self.runtime, 'functions_configuration', return_value={'SB_JWT_SECRET': 'j' * 64, 'SB_STORAGE_URL': 'http://172.30.4.1:25433', 'SUPABASE_ANON_KEY': 'f', 'SUPABASE_SERVICE_ROLE_KEY': 'f'}), \
                patch.object(self.runtime, 'image', return_value=('ref', 'sha256:fn')), \
                patch.object(runtime.subprocess, 'run', side_effect=lambda command, **k: calls.append(command) or SimpleNamespace(returncode=0)), \
                patch.object(runtime.lab, 'docker', return_value=SimpleNamespace(stdout=json.dumps(self.image_env), returncode=0)):
            self.assertTrue(self.runtime.functions_kept(E, self.runtime.values['environments'][E], self.container(), self.network))
        self.assertEqual(calls, [['git', 'diff', '--quiet', 'b' * 40, 'HEAD', '--', 'lab/functions/main']])

    def test_a_key_with_another_header_is_not_one_of_ours(self):
        good = runtime.service_token('j' * 64, {'role': 'anon', 'iss': 'sbarbase'}, 10 ** 9)
        self.assertTrue(runtime.valid_service_token(good, 'j' * 64, 'anon'))
        head, body, _ = good.split('.')
        other = runtime.base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b'=').decode()
        signature = runtime.base64.urlsafe_b64encode(runtime.hmac.new(b'j' * 64, (other + '.' + body).encode(), runtime.hashlib.sha256).digest()).rstrip(b'=').decode()
        self.assertFalse(runtime.valid_service_token(other + '.' + body + '.' + signature, 'j' * 64, 'anon'))

    def test_main_service_code_that_changed_since_it_started_recreates_it(self):
        self.diff = 1
        self.assertFalse(self.kept(self.container()))
        self.diff = 0
        self.runtime.kept = dict(self.runtime.kept, **{'from': None})
        self.assertFalse(self.kept(self.container()))

    def test_a_kept_container_is_neither_removed_nor_connected_again(self):
        calls = []
        item = self.container()
        found = lambda kind, name: item if kind == 'container' and name.endswith('-functions') else ({'Internal': True, 'Labels': {'io.sbarbase.functions': E}} if name == self.network else None)
        with patch.object(runtime, 'STATE', self.state), patch.object(runtime, 'PRIVATE', self.state / 'private'), \
                patch.object(runtime, 'inspect', side_effect=found), patch.object(runtime, 'ensure_egress'), \
                patch.object(runtime, 'functions_address', return_value=('172.30.4.1', '172.30.4.0/24')), \
                patch.object(runtime, 'functions_code', return_value=self.state / 'code'), patch.object(runtime, 'functions_private', return_value=self.state / 'private'), \
                patch.object(self.runtime, 'functions_kept', return_value=True), patch.object(self.runtime, 'launch'), \
                patch.object(self.runtime, 'endpoint', return_value='http://10.0.0.9:9000'), patch.object(runtime, 'http', return_value=(404, b'')), \
                patch.object(runtime.lab, 'secure_file', side_effect=lambda path, text: Path(path).write_text(text)), \
                patch.object(runtime.lab, 'docker', side_effect=lambda *a, **k: calls.append(a) or SimpleNamespace(stdout='', returncode=0)):
            self.runtime.functions_start(E)
        self.assertFalse(any(call[:2] == ('rm', '-f') for call in calls))
        self.assertFalse(any(call[:2] == ('network', 'connect') and call[2] == runtime.EGRESS for call in calls))


class KeptFunctionsStartTests(FunctionsTests):
    def test_a_kept_container_survives_the_whole_start_path_with_its_older_keys(self):
        # Nothing about the start is patched but Docker and the host: functions_kept and launch run for real.
        calls = []
        # The settings of a container started earlier: the same, with keys made then.
        made = self.runtime.functions_configuration(E, self.runtime.values['environments'][E], '172.30.4.1')
        time.sleep(1.1)
        item = self.container(env=made)
        found = lambda kind, name: item if kind == 'container' and name.endswith('-functions') else (
            {'Internal': True, 'Labels': {'io.sbarbase.functions': E}} if name == self.network else None)
        with patch.object(runtime, 'STATE', self.state), patch.object(runtime, 'PRIVATE', self.state / 'private'), \
                patch.object(runtime, 'inspect', side_effect=found), patch.object(runtime, 'ensure_egress'), \
                patch.object(runtime, 'functions_address', return_value=('172.30.4.1', '172.30.4.0/24')), \
                patch.object(self.runtime, 'image', return_value=('ref', 'sha256:fn')), \
                patch.object(runtime.subprocess, 'run', return_value=SimpleNamespace(returncode=0)), \
                patch.object(self.runtime, 'endpoint', return_value='http://10.0.0.9:9000'), patch.object(runtime, 'http', return_value=(404, b'')), \
                patch.object(runtime.lab, 'secure_file', side_effect=lambda path, text: Path(path).write_text(text)), \
                patch.object(runtime.lab, 'docker', side_effect=lambda *a, **k: calls.append(a) or SimpleNamespace(
                    stdout=json.dumps(self.image_env) if a[:2] == ('image', 'inspect') else '', returncode=0)):
            item['Mounts'] = [{'Source': str(source), 'Destination': target, 'RW': False} for source, target in runtime.functions_binds(E)]
            item['Config']['Labels'] = {'io.sbarbase.spec': runtime.launch_spec('functions', 'ref', '384m', .5, 'production.functions', self.network,
                                                                                 binds=runtime.functions_binds(E), command=runtime.FUNCTIONS_COMMAND)}
            self.runtime.functions_start(E)
        self.assertFalse([call for call in calls if call[0] in ('run', 'rm', 'stop')])
        # Noted as run by this start, so retire_unadopted leaves it alone.
        self.assertIn('id-fn', self.runtime.touched)


class RetireTests(RuntimeFixture):
    def test_a_kept_container_this_start_did_not_run_is_stopped_and_studio_left_to_its_reset(self):
        self.runtime.kept = {'containers': {'id-rest': {'name': f'sbarbase-durable-{E}-rest'}, 'id-old': {'name': 'sbarbase-durable-gone'},
                                            'id-studio': {'name': f'sbarbase-durable-{E}-studio'}, 'id-stopped': {'name': 'sbarbase-durable-x'}}}
        self.runtime.adopted('id-rest')
        calls = []
        found = lambda kind, cid: {'State': {'Running': cid != 'id-stopped'}}
        with patch.object(runtime, 'inspect', side_effect=found), contextlib.redirect_stdout(io.StringIO()), \
                patch.object(runtime.lab, 'docker', side_effect=lambda *a, **k: calls.append(a)):
            self.runtime.retire_unadopted()
        self.assertEqual(calls, [('stop', 'id-old')])

    def test_launch_notes_what_it_starts_and_labels_how(self):
        self.assertNotEqual(runtime.launch_spec('functions', 'ref', '384m', .5, 'production.functions', 'n'),
                            runtime.launch_spec('functions', 'ref', '384m', .5, 'production.functions', 'n', command=('other',)))
        self.assertEqual(runtime.launch_spec('rest', 'ref', '256m', .25, 'production', 'n'), runtime.launch_spec('rest', 'ref', '256m', .25, 'production', 'n'))
        self.assertNotEqual(runtime.launch_spec('rest', 'ref', '256m', .25, 'production', 'n'), runtime.launch_spec('rest', 'ref', '256m', .25, 'production', 'n', binds=[('a', 'b')]))


class ReplaceTests(RuntimeFixture):
    def test_a_running_container_that_is_replaced_stops_before_it_is_removed(self):
        calls = []
        for running in (True, False):
            calls.clear()
            with patch.object(runtime, 'inspect', return_value={'Id': 'x', 'State': {'Running': running}}), \
                    patch.object(runtime.lab, 'docker', side_effect=lambda *a, **k: calls.append(a)):
                runtime.retire('sbarbase-durable-x')
            self.assertEqual(calls, ([('stop', 'sbarbase-durable-x')] if running else []) + [('rm', '-f', 'sbarbase-durable-x')])

    def test_every_path_that_replaces_a_container_goes_through_retire_or_launch(self):
        source = Path(runtime.__file__).read_text()
        # The only direct removals left are launch's own (after its stop) and the paths that turn a service off.
        for start in ('    def realtime_start(', '    def functions_start('):
            body = source[source.index(start):]
            body = body[:body.index('\n    def ', 1)]
            self.assertNotIn("'rm', '-f'", body)
            self.assertIn('retire(name)', body)

    def test_the_database_restart_for_its_connection_limit_checkpoints_first(self):
        order = []
        self.runtime.values = {'environments': {}}
        answers = iter(['100', str(runtime.connection_budget.MAX_CONNECTIONS)])
        self.runtime.sql = Mock(side_effect=lambda query, *a, **k: SimpleNamespace(returncode=0, stdout=next(answers) if 'current_setting' in query else ''))
        self.runtime.wait_database = Mock()
        with patch.object(runtime, 'inspect', return_value=None), patch.object(runtime, 'checkpoint', side_effect=lambda: order.append('checkpoint')), \
                patch.object(runtime.lab, 'docker', side_effect=lambda *a, **k: order.append(a[0])), contextlib.redirect_stdout(io.StringIO()):
            self.runtime.ensure_connection_capacity()
        self.assertEqual(order, ['checkpoint', 'restart'])

    def test_the_start_retires_what_it_did_not_adopt_after_the_environments(self):
        source = Path(runtime.__file__).read_text()
        start = source[source.index('    def start(self):'):]
        start = start[:start.index('\n    def ', 1)]
        self.assertLess(start.index('self.resume_published_environments()'), start.index('self.retire_unadopted()'))


class StopTests(Folder):
    def test_services_stop_first_then_a_checkpoint_then_a_bounded_database_stop(self):
        calls = []

        def docker(*args, **kwargs):
            calls.append(args)
            if args[:2] == ('ps', '-q'):
                return SimpleNamespace(stdout='id-db\n' if any(str(a).startswith('name=') for a in args) else 'id-auth\nid-db\nid-rest\n', returncode=0)
            return SimpleNamespace(stdout='', returncode=0)
        executed = []
        (self.state / kept_runtime.NAME).write_text('{}')
        with patch.object(runtime.lab, 'docker', side_effect=docker), patch.object(runtime, 'STATE', self.state), \
                patch.object(runtime.subprocess, 'run', side_effect=lambda command, **k: executed.append((command, k)) or SimpleNamespace(returncode=0)):
            runtime.stop()
        stops = [call for call in calls if call[0] == 'stop']
        # Full ids on both queries, so the database is told apart from the services by the same id form.
        self.assertTrue(all('--no-trunc' in call for call in calls if call[:2] == ('ps', '-q')))
        self.assertEqual(stops, [('stop', 'id-auth', 'id-rest'), ('stop', '-t', str(runtime.STOP_DATABASE_SECONDS), 'id-db')])
        self.assertIn('CHECKPOINT;', executed[0][0])
        self.assertEqual(executed[0][1]['timeout'], runtime.CHECKPOINT_SECONDS)
        # A stopped runtime keeps no record of containers a planned restart left.
        self.assertFalse((self.state / kept_runtime.NAME).exists())
        # The services' 10 s, the checkpoint and the database stay inside the supervisor's 90 s stop stage.
        self.assertLessEqual(10 + runtime.CHECKPOINT_SECONDS + runtime.STOP_DATABASE_SECONDS + 15, dev.STOP_STAGE_SECONDS)
        self.assertEqual(leftover_runtime.STOP_SECONDS, dev.STOP_STAGE_SECONDS)

    def test_a_checkpoint_that_times_out_still_stops_the_database(self):
        calls = []

        def docker(*args, **kwargs):
            calls.append(args)
            return SimpleNamespace(stdout='id-db\n' if args[:2] == ('ps', '-q') else '', returncode=0)

        def slow(command, **kwargs):
            raise runtime.subprocess.TimeoutExpired(command, kwargs['timeout'])
        with patch.object(runtime.lab, 'docker', side_effect=docker), patch.object(runtime, 'STATE', self.state), \
                patch.object(runtime.subprocess, 'run', side_effect=slow):
            runtime.stop()
        self.assertIn(('stop', '-t', str(runtime.STOP_DATABASE_SECONDS), 'id-db'), calls)


class StartHeadroomTests(Folder):
    def test_the_start_counts_back_what_kept_containers_hold(self):
        target = runtime.Runtime.__new__(runtime.Runtime)
        target.values = {'environments': {}, 'admin': 'a' * 64}
        target.hba_writer = SimpleNamespace(startup=SimpleNamespace(verify=lambda: None))
        reached = []
        target.launch = Mock(side_effect=lambda *a, **k: reached.append('launch') or (_ for _ in ()).throw(StopIteration))
        meminfo = 'MemAvailable:    1048576 kB\n'
        with patch.object(runtime, 'STATE', self.state), patch.object(runtime.effect_receipt, 'require_settled'), \
                patch.object(runtime.lab, 'docker', return_value=SimpleNamespace(stdout='', returncode=0)), \
                patch.object(runtime.lab, 'Path', side_effect=lambda p: SimpleNamespace(read_text=lambda: meminfo)), \
                patch.object(runtime.resource_policy, 'start_placement', return_value=(1500, 1)), \
                patch.object(runtime.resource_policy, 'START_RESERVE_MIB', 0), \
                patch.object(runtime, 'inspect', return_value={'Id': 'network'}):
            with patch.object(runtime.kept_runtime, 'current', return_value=(None, 'no record')):
                with self.assertRaisesRegex(RuntimeError, 'Insufficient runtime memory headroom'):
                    target.start()
            # 1024 MiB available against a 1500 MiB placement: exactly the missing 476 MiB credited lets
            # the start through, one MiB less does not, so the credit is counted once and no more.
            with patch.object(runtime.kept_runtime, 'current', return_value=({'containers': {'a': {}}}, None)), \
                    patch.object(runtime.kept_runtime, 'credit_mib', return_value=475):
                with self.assertRaisesRegex(RuntimeError, 'Insufficient runtime memory headroom'):
                    target.start()
            with patch.object(runtime.kept_runtime, 'current', return_value=({'containers': {'a': {}}}, None)) as current, \
                    patch.object(runtime.kept_runtime, 'credit_mib', return_value=476):
                with self.assertRaises(StopIteration):
                    target.start()
            # A check after the settle in the same start: the record's age gets the later grace.
            self.assertEqual(current.call_args.kwargs.get('grace'), runtime.kept_runtime.LATER_GRACE)
        self.assertEqual(reached, ['launch'])

    def test_a_record_that_no_longer_holds_at_the_start_stops_it(self):
        target = runtime.Runtime.__new__(runtime.Runtime)
        target.values = {'environments': {}, 'admin': 'a' * 64}
        target.hba_writer = SimpleNamespace(startup=SimpleNamespace(verify=lambda: None))
        (self.state / kept_runtime.NAME).write_text('{}')
        meminfo = 'MemAvailable:    9048576 kB\n'
        with patch.object(runtime, 'STATE', self.state), patch.object(runtime.effect_receipt, 'require_settled'), \
                patch.object(runtime.lab, 'docker', return_value=SimpleNamespace(stdout='', returncode=0)), \
                patch.object(runtime.lab, 'Path', side_effect=lambda p: SimpleNamespace(read_text=lambda: meminfo)), \
                patch.object(runtime.resource_policy, 'start_placement', return_value=(1500, 1)), \
                patch.object(runtime.kept_runtime, 'current', return_value=(None, 'an earlier start took the record and did not finish')):
            with self.assertRaisesRegex(RuntimeError, 'can no longer be adopted'):
                target.start()


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.out, self.staged, self.previous = self.root / 'ui', self.root / 'ui-next', self.root / 'ui-previous'

    def page(self, folder, text):
        folder.mkdir()
        (folder / 'assets').mkdir()
        (folder / 'assets' / 'app.js').write_text(text)
        (folder / 'index.html').write_text('<script src="/assets/app.js"></script>')

    def test_a_usable_staged_build_takes_the_place_of_the_served_one(self):
        self.page(self.out, 'old')
        self.page(self.staged, 'new')
        with patch.object(console_build_check, 'is_fresh', return_value=(True, 'fresh')):
            self.assertTrue(console_build_check.adopt_staged(self.out, self.staged, self.previous))
        self.assertEqual((self.out / 'assets' / 'app.js').read_text(), 'new')
        self.assertFalse(self.staged.exists() or self.previous.exists())

    def test_a_stale_staged_build_is_removed_and_the_served_one_kept(self):
        self.page(self.out, 'old')
        self.page(self.staged, 'new')
        with patch.object(console_build_check, 'is_fresh', return_value=(False, 'stale')):
            self.assertFalse(console_build_check.adopt_staged(self.out, self.staged, self.previous))
        self.assertEqual((self.out / 'assets' / 'app.js').read_text(), 'old')
        self.assertFalse(self.staged.exists())

    def test_shared_sources_the_console_imports_make_a_build_stale(self):
        built = self.root / 'checkout'
        (built / '.lab' / 'ui').mkdir(parents=True)
        (built / '.lab' / 'ui' / 'index.html').write_text('<script src="/a.js"></script>')
        (built / '.lab' / 'ui' / 'a.js').write_text('')
        (built / 'src').mkdir()
        os.utime(built / '.lab' / 'ui' / 'index.html', (1000, 1000))
        (built / 'src' / 'shared.ts').write_text('')
        with patch.object(console_build_check, 'ROOT', built):
            self.assertFalse(console_build_check.is_fresh(built / '.lab' / 'ui')[0])

    def test_nothing_staged_changes_nothing(self):
        self.assertFalse(console_build_check.adopt_staged(self.out, self.staged, self.previous))


if __name__ == '__main__':
    unittest.main()
