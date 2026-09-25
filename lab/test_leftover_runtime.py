"""A start after an unclean stop stops the leftover owned runtime, unless someone owns it or
pending authority state needs reconciliation."""
import contextlib
import fcntl
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import leftover_runtime


class Fixture(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name) / 'upstream'
        self.state.mkdir()
        self.stops = []
        self.after = []

    def hold(self, name):
        """A live owner: another open file of the lock, holding it, as another process would."""
        handle = (self.state / name).open('a')
        self.addCleanup(handle.close)
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle

    def settle(self, running=('c1', 'c2'), stop_result=0, after=(), **options):
        answers = iter([list(running) if running is not None else None, list(after)])

        def stop():
            # While the stop runs, every free owner lock is held by the settle step itself.
            for name in leftover_runtime.LOCKS:
                if name in options.get('caller_holds', ()):
                    continue
                with (self.state / name).open('a') as other, self.assertRaises(BlockingIOError):
                    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.stops.append('stop')
            return stop_result
        return leftover_runtime.settle(self.state, running=lambda: next(answers), stop=stop,
                                       say=lambda *args, **kwargs: None, **options)


class DecisionTests(Fixture):
    def test_nothing_running_is_left_alone_and_takes_no_lock(self):
        self.assertEqual(self.settle(running=()), ('nothing', None))
        self.assertEqual(self.stops, [])
        self.assertEqual(list(self.state.iterdir()), [])

    def test_docker_that_cannot_be_asked_changes_nothing(self):
        self.assertEqual(self.settle(running=None), ('unknown', None))
        self.assertEqual(self.stops, [])

    def test_running_containers_with_every_lock_free_are_stopped(self):
        self.assertEqual(self.settle(), ('stopped', None))
        self.assertEqual(self.stops, ['stop'])

    def test_a_held_supervisor_or_worker_lock_declines(self):
        for name in leftover_runtime.LOCKS:
            with self.subTest(name=name):
                handle = self.hold(name)
                outcome, reason = self.settle()
                handle.close()
                self.assertEqual(outcome, 'decline')
                self.assertIn(name, reason)
                self.assertIn('still owns them', reason)
        self.assertEqual(self.stops, [])

    def test_pending_authority_state_keeps_blocking(self):
        for pending in ('worker-effect.json', 'hba-operation.json', 'hba-migration', 'targets/e_x/hba-operation.json'):
            with self.subTest(pending=pending):
                path = self.state / pending
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('{}')
                outcome, reason = self.settle()
                path.unlink()
                self.assertEqual(outcome, 'decline')
                self.assertIn(pending, reason)
                self.assertIn('reconciliation', reason)
        self.assertEqual(self.stops, [])

    def test_pending_state_with_nothing_running_is_not_this_steps_refusal(self):
        (self.state / 'worker-effect.json').write_text('{}')
        self.assertEqual(self.settle(running=()), ('nothing', None))

    def test_a_failed_or_incomplete_stop_raises(self):
        with self.assertRaisesRegex(leftover_runtime.StopFailed, 'could not be stopped'):
            self.settle(stop_result=1)
        with self.assertRaisesRegex(leftover_runtime.StopFailed, 'still run'):
            self.settle(after=('c1',))

    def test_the_caller_that_holds_the_supervisor_and_worker_locks_is_not_refused_by_them(self):
        """lab/dev.py holds both: flock refuses a second open file even in one process."""
        supervisor, worker = self.hold('supervisor.lock'), self.hold('worker.lock')
        self.assertEqual(self.settle(caller_holds=('supervisor.lock', 'worker.lock')), ('stopped', None))
        # The effect lock is still probed for the caller.
        effect = self.hold('effect.lock')
        outcome, reason = self.settle(caller_holds=('supervisor.lock', 'worker.lock'))
        self.assertEqual(outcome, 'decline')
        self.assertIn('effect.lock', reason)
        for handle in (supervisor, worker, effect):
            handle.close()

    def test_the_decision_alone(self):
        self.assertEqual(leftover_runtime.decide([], ['worker-effect.json'], ['supervisor.lock']), ('nothing', None))
        self.assertEqual(leftover_runtime.decide(['c'], [], []), ('stop', None))
        # A live owner is named before pending state: stopping its runtime is never an option.
        self.assertIn('supervisor.lock', leftover_runtime.decide(['c'], ['hba-migration'], ['supervisor.lock'])[1])


class CommandTests(Fixture):
    def test_the_stop_is_the_supervisors_own_stop_and_removes_nothing(self):
        calls = []

        def run(argv, **kwargs):
            calls.append((list(argv), kwargs.get('cwd')))
            return type('R', (), {'returncode': 0, 'stdout': ''})()
        with patch.object(leftover_runtime.subprocess, 'run', run):
            self.assertEqual(leftover_runtime.installation_stop(), 0)
            self.assertEqual(leftover_runtime.docker_ps(), [])
        self.assertEqual(calls[0], (['/usr/bin/python3', 'lab/installation_runtime.py', 'stop'], leftover_runtime.ROOT))
        self.assertEqual(calls[1][0], ['docker', 'ps', '-q', '--filter', 'label=io.sbarbase.owner=durable-upstream'])
        for argv, _ in calls:
            self.assertFalse({'rm', 'volume', 'down', 'kill', 'prune'} & set(argv))

    def test_exit_status_and_reasons(self):
        cases = (
            (('nothing', None), None, 0),
            (('unknown', None), None, 0),
            (('stopped', None), None, 0),
            (('decline', 'why'), None, 1),
            (None, leftover_runtime.StopFailed('broke'), 1),
        )
        for result, error, code in cases:
            with self.subTest(result=result, error=error):
                with patch.object(leftover_runtime, 'settle', side_effect=error, return_value=result), \
                        patch.object(leftover_runtime.os, 'chdir'), \
                        contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                    self.assertEqual(leftover_runtime.main(), code)
                if code:
                    self.assertTrue(err.getvalue().strip())


class SupervisorStartTests(Fixture):
    """lab/dev.py runs it with its own locks held, outside the start whose failure takes an
    upgrade's way back."""

    def test_a_decline_lets_the_start_go_on_and_a_failed_stop_refuses_it(self):
        import dev
        with patch.object(leftover_runtime, 'settle', return_value=('decline', 'pending receipt')) as settle, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            dev.settle_leftover()
        self.assertEqual(settle.call_args.kwargs['caller_holds'], ('supervisor.lock', 'worker.lock'))
        self.assertIn('pending receipt', err.getvalue())
        with patch.object(leftover_runtime, 'settle', side_effect=leftover_runtime.StopFailed('broke')):
            with self.assertRaisesRegex(SystemExit, 'broke'):
                dev.settle_leftover()

    def test_it_runs_after_the_locks_and_before_anything_of_the_start(self):
        import dev
        source = Path(dev.__file__).read_text()
        main = source[source.index('def main():'):]
        self.assertLess(main.index("'Stop the existing manual worker"), main.index('settle_leftover()'))
        self.assertLess(main.index('settle_leftover()'), main.index('try:\n            # Before the settle stage'))


if __name__ == '__main__':
    unittest.main()
