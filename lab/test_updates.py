"""The update channel on the supervisor's side (lab/updates.py and lab/dev.py schedule_updates):
settings, the request file, the schedule, automatic updates, the restart exit code and the
notifications. No test starts a container or reaches the network: children are stand-ins."""
import datetime
import json
import os
import stat
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import dev
import notification_producers
import updates
import upgrade
from test_notification_producers import ProducerCase
from test_upgrade import Checkout, locked, store

ROOT = Path(__file__).resolve().parents[1]
ZONE = datetime.timezone(datetime.timedelta(hours=4))
COMMIT = 'a' * 40


def at(hour, minute=0, day=25):
    return datetime.datetime(2026, 9, day, hour, minute, tzinfo=ZONE)


def release(**overrides):
    return {'version': '0.2.0', 'tag': 'v0.2.0', 'commit': 'b' * 40, 'class': 'safe', 'signed': True, 'reasons': [],
            'notes': {'en': 'Faster.', 'ar': 'أسرع.'}, 'changes': [], 'minimum_from': '0.1.0', 'migrations': [], **overrides}


def document(available=None, refusals=(), commit=COMMIT):
    return {'current': {'version': '0.1.0', 'commit': commit}, 'available': release() if available is None else available,
            'refusals': list(refusals), 'checked_at': '2026-09-25T06:00:00+00:00'}


CURRENT = {'version': '0.1.0', 'commit': COMMIT}


class Private(unittest.TestCase):
    """Every update file in a private temporary directory, and the upgrade state beside it."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name) / 'upgrades'
        for item in (patch.object(updates, 'UPGRADES', self.folder), patch.object(upgrade, 'STATE_FILE', self.folder / 'state.json')):
            item.start()
            self.addCleanup(item.stop)

    def put(self, name, value):
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / name).write_text(json.dumps(value))

    def get(self, name):
        return json.loads((self.folder / name).read_text())

    def entry(self, version):
        return updates.ledger()['versions'].get(version, updates.ENTRY)


class SettingsTests(Private):
    def test_defaults_until_the_operator_saves_and_a_damaged_file_reads_as_defaults(self):
        self.assertEqual(updates.load_settings(), {'check': True, 'automatic': False, 'window': {'start': '03:00', 'end': '05:00'}})
        saved = updates.save_settings({'check': True, 'automatic': True, 'window': {'start': '23:00', 'end': '01:00'}})
        self.assertEqual(updates.load_settings(), saved)
        self.assertEqual(stat.S_IMODE((self.folder / 'settings.json').stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.folder.stat().st_mode), 0o700)
        (self.folder / 'settings.json').write_text('{"check": true, "automatic": true')
        self.assertFalse(updates.load_settings()['automatic'])

    def test_validation_is_strict(self):
        good = {'check': True, 'automatic': False, 'window': {'start': '03:00', 'end': '05:00'}}
        for change, message in (({'window': {'start': '03:00', 'end': '03:00'}}, 'different'),
                                ({'window': {'start': '24:00', 'end': '03:00'}}, 'valid'),
                                ({'window': {'start': '3:00', 'end': '05:00'}}, 'valid'),
                                ({'window': {'start': '03:00'}}, 'start and an end'),
                                ({'check': 1}, 'on or off'),
                                ({'check': False, 'automatic': True}, 'checking'),
                                ({'extra': True}, 'exactly')):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, message):
                updates.validate_settings({**good, **change})
        self.assertEqual(updates.validate_settings(good), good)

    def test_the_window_holds_its_start_not_its_end_and_may_cross_midnight(self):
        day = {'start': '03:00', 'end': '05:00'}
        self.assertEqual([updates.in_window(day, at(hour, minute)) for hour, minute in ((2, 59), (3, 0), (4, 59), (5, 0))],
                         [False, True, True, False])
        night = {'start': '23:00', 'end': '01:00'}
        self.assertEqual([updates.in_window(night, at(hour, minute)) for hour, minute in ((22, 59), (23, 30), (0, 30), (1, 0), (12, 0))],
                         [False, True, True, False, False])


class RequestTests(Private):
    def test_one_request_at_a_time_created_privately(self):
        first = updates.create_request('apply', '0.2.0', 'console', at(3))
        self.assertNotIn('tag', first)
        self.assertIsNone(updates.create_request('check', moment=at(3)))
        self.assertEqual(updates.read_request()['id'], first['id'])
        self.assertEqual(stat.S_IMODE((self.folder / 'request.json').stat().st_mode), 0o600)
        self.assertEqual([path.name for path in self.folder.iterdir() if path.name.endswith('.tmp')], [])

    def test_a_finished_request_is_kept_as_the_last_one_and_frees_the_slot(self):
        request = updates.create_request('apply', '0.2.0', moment=at(3))
        updates.update_request(request, state='running', started_at=updates.stamp(at(3)))
        self.assertEqual(updates.read_request()['state'], 'running')
        updates.finish_request(request, 'failed', 'A backup or restore is running.', at(3, 1))
        self.assertIsNone(updates.read_request())
        self.assertEqual(self.get('last-request.json')['state'], 'failed')
        self.assertEqual(self.get('last-request.json')['detail'], 'A backup or restore is running.')
        # An update for a request that is no longer in the slot changes nothing.
        self.assertIsNone(updates.update_request(request, state='running'))
        self.assertIsNotNone(updates.create_request('check', moment=at(3, 2)))

    def test_on_start_a_request_the_previous_process_left_is_settled(self):
        # Running when the process ended, and the upgrade record shows the checkout moved: done.
        request = updates.create_request('apply', '0.2.0', moment=at(3))
        updates.update_request(request, state='running', started_at=updates.stamp(at(3, 1)))
        moved = {'phase': 'applied', 'from': 'c' * 40, 'to': 'b' * 40, 'started_at': updates.stamp(at(3, 5))}
        self.assertEqual(updates.settle(moved, at(3, 10))['state'], 'done')
        # Running, but the record is older than the request: it never got there.
        request = updates.create_request('apply', '0.2.0', moment=at(4))
        updates.update_request(request, state='running', started_at=updates.stamp(at(4, 1)))
        settled = updates.settle(moved, at(4, 10))
        self.assertEqual((settled['state'], settled['detail']), ('failed', updates.MESSAGES['interrupted']))
        # A rollback that recorded its way back went through.
        request = updates.create_request('rollback', moment=at(5))
        updates.update_request(request, state='running', started_at=updates.stamp(at(5)))
        self.assertEqual(updates.settle({**moved, 'rollback_at': updates.stamp(at(5, 2))}, at(5, 3))['state'], 'done')
        # Asked for and never picked up within the hour: not carried out later.
        updates.create_request('apply', '0.2.0', moment=at(6))
        self.assertEqual(updates.settle(None, at(7, 1))['detail'], updates.MESSAGES['expired'])
        # Asked for moments ago: still waiting.
        updates.create_request('check', moment=at(8))
        self.assertEqual(updates.settle(None, at(8, 5))['state'], 'requested')
        self.assertIsNotNone(updates.read_request())

    def test_an_automatic_try_the_previous_process_never_saw_end_is_spent_when_it_passed_its_point_of_no_return(self):
        for passed, version in ((False, '0.2.0'), (True, '0.3.0')):
            path = self.folder / 'request.json'
            path.unlink(missing_ok=True)
            request = updates.create_request('apply', version, 'automatic', at(3))
            updates.update_request(request, state='running', started_at=updates.stamp(at(3)))
            self.put('outcome.json', {'request': request['id'], 'kind': 'start', 'passed': passed})
            self.assertEqual(updates.settle(None, at(3, 10))['detail'], updates.MESSAGES['interrupted'])
            self.assertEqual(self.entry(version)['spent'], passed)

    def test_the_sentences_are_the_ones_the_console_answers_with(self):
        source = (ROOT / 'src' / 'control' / 'updates.ts').read_text()
        for name, sentence in updates.MESSAGES.items():
            if name not in ('expired', 'interrupted'):
                self.assertIn(f"{name}:'{sentence}'", source, name)

    def test_the_detail_of_a_failed_child_comes_from_its_outcome(self):
        self.put('outcome.json', {'request': 'r1', 'refusals': ['A backup or restore is running; wait for it to finish'],
                                  'error': 'Nothing was changed'})
        self.assertIsNone(updates.outcome('r2'))
        self.assertEqual(updates.failure(updates.outcome('r1')),
                         'A backup or restore is running; wait for it to finish. Nothing was changed.')
        self.assertEqual(updates.failure({'refusals': [], 'error': 'The backup before the upgrade failed; nothing was changed'}),
                         'The backup before the upgrade failed; nothing was changed.')
        self.assertEqual(updates.failure(None), 'It stopped without saying why.')
        self.assertLessEqual(len(updates.failure({'error': 'x' * 1000})), 400)


class ScheduleDecisionTests(Private):
    def test_the_first_check_waits_then_every_six_hours_sooner_after_a_failure(self):
        since = at(10)
        self.assertFalse(updates.check_due(None, at(10, 4), since))
        self.assertTrue(updates.check_due(None, at(10, 5), since))
        done = {'attempted_at': updates.stamp(at(10, 5)), 'error': None, 'failures': 0}
        self.assertFalse(updates.check_due(done, at(16, 4), since))
        self.assertTrue(updates.check_due(done, at(16, 5), since))
        failed = {**done, 'error': 'offline', 'failures': 1}
        self.assertTrue(updates.check_due(failed, at(10, 35), since))
        self.assertFalse(updates.check_due({**failed, 'failures': 3}, at(12, 4), since))
        self.assertTrue(updates.check_due({**failed, 'failures': 3}, at(12, 5), since))
        self.assertFalse(updates.check_due({**failed, 'failures': 9}, at(16, 4), since))
        # Right after an upgrade the result about the previous version was set aside: check again.
        self.assertTrue(updates.check_due(done, at(10, 30), since, result=False))
        # But an offline host with no result keeps its backoff.
        offline = {'attempted_at': updates.stamp(at(10, 30)), 'error': 'offline', 'failures': 1}
        self.assertFalse(updates.check_due(offline, at(10, 31), since, result=False))
        self.assertTrue(updates.check_due(offline, at(11, 0), since, result=False))

    def test_an_offline_check_is_a_failure_that_keeps_the_release_it_knew_of(self):
        # The channel writes no result when the source cannot be read: the one before stays.
        self.put('available.json', document())
        error, kept = updates.finish_check(1, {'error': 'The release source could not be read: git ls-remote failed'}, at(10))
        self.assertTrue(error.startswith('The release source could not be read'))
        self.assertEqual(kept['available']['version'], '0.2.0')
        self.assertEqual(self.get('check.json')['failures'], 1)
        error, _ = updates.finish_check(1, {'error': 'git fetch failed: timeout'}, at(11))
        self.assertEqual((error, self.get('check.json')['failures']), ('git fetch failed: timeout.', 2))
        self.assertEqual(updates.finish_check(0, {}, at(12))[0], None)
        self.assertEqual(self.get('check.json'), {'attempted_at': updates.stamp(at(12)), 'error': None, 'failures': 0})

    def test_automatic_updates_apply_only_a_clear_release_inside_the_window_once(self):
        settings = {'check': True, 'automatic': True, 'window': {'start': '23:00', 'end': '01:00'}}

        def chosen(moment=at(0, 30), settings=settings, checked=None, state=None, entry=None, backup=False):
            self.put('ledger.json', {'versions': {'0.2.0': entry} if entry else {}})
            found = updates.automatic_release(settings, checked or document(), state, moment, backup)
            return found and found['version']
        self.assertEqual(chosen(), '0.2.0')
        self.assertEqual(chosen(at(23, 30, day=24)), '0.2.0')
        self.assertIsNone(chosen(at(12)))
        self.assertIsNone(chosen(settings={**settings, 'automatic': False}))
        self.assertIsNone(chosen(backup=True))
        self.assertIsNone(chosen(entry={'spent': True}))
        self.assertIsNone(chosen(entry={'rolled_back': True}))
        self.assertEqual(chosen(entry={'announced': True}), '0.2.0')
        self.assertIsNone(chosen(checked=document(release(signed=False))))
        self.assertIsNone(chosen(checked=document(release(**{'class': 'rebuild'}))))
        # A release that migrates environment databases is never installed automatically.
        self.assertIsNone(chosen(checked=document(release(**{'class': 'attended'}))))
        self.assertIsNone(chosen(checked=document(refusals=['A backup or restore is running'])))
        self.assertIsNone(chosen(state={'phase': 'applied', 'to': 'b' * 40, 'started_at': 'x'}))


class RefusalTests(Private):
    def test_an_attended_release_needs_the_operators_acknowledgement(self):
        checked = document(release(**{'class': 'attended'}))
        self.assertEqual(updates.apply_refusal('0.2.0', checked, None), updates.MESSAGES['acknowledge'])
        self.assertEqual(updates.apply_refusal('0.2.0', checked, None, acknowledged='yes'), updates.MESSAGES['acknowledge'])
        self.assertIsNone(updates.apply_refusal('0.2.0', checked, None, acknowledged=True))
        for kind in ('rebuild', 'manual'):
            self.assertEqual(updates.apply_refusal('0.2.0', document(release(**{'class': kind})), None, acknowledged=True),
                             updates.MESSAGES['class'])

    def test_notes_about_releases_passed_over_never_refuse_the_one_on_offer(self):
        checked = {**document(), 'skipped': ['v0.3.0 was passed over: v0.3.0 needs at least version 0.2.0'],
                   'newest': {'version': '0.3.0', 'tag': 'v0.3.0', 'class': 'safe', 'signed': True, 'reasons': ['x']}}
        self.assertIsNone(updates.apply_refusal('0.2.0', checked, None))
        settings = {'check': True, 'automatic': True, 'window': {'start': '23:00', 'end': '01:00'}}
        found = updates.automatic_release(settings, checked, None, at(0, 30), False)
        self.assertEqual(found['version'], '0.2.0')


class TriesTests(Private):
    settings = {'check': True, 'automatic': True, 'window': {'start': '23:00', 'end': '01:00'}}

    def chosen(self, moment, state=None):
        found = updates.automatic_release(self.settings, document(), state, moment, False)
        return found and found['version']

    def test_a_try_that_stopped_before_the_backup_is_tried_again_three_times_at_most(self):
        self.assertEqual(self.chosen(at(0, 0)), '0.2.0')
        self.assertEqual(updates.begin_automatic('0.2.0', at(0, 0)), 1)
        # Backing off: 10 minutes after the first try, 20 after the second.
        self.assertIsNone(self.chosen(at(0, 9)))
        self.assertEqual(self.chosen(at(0, 10)), '0.2.0')
        updates.begin_automatic('0.2.0', at(0, 10))
        self.assertIsNone(self.chosen(at(0, 29)))
        self.assertEqual(self.chosen(at(0, 30)), '0.2.0')
        updates.begin_automatic('0.2.0', at(0, 30))
        self.assertIsNone(self.chosen(at(0, 59)))
        self.assertEqual((self.entry('0.2.0')['tries'], self.entry('0.2.0')['spent']), (3, False))
        # Still inside the window only.
        self.put('ledger.json', {'versions': {'0.2.0': {'tries': 1, 'last': updates.stamp(at(0, 0))}}})
        self.assertIsNone(self.chosen(at(1, 30)))

    def test_a_try_is_spent_once_it_passed_its_point_of_no_return_without_moving_the_checkout(self):
        request = {'id': 'r1', 'kind': 'apply', 'trigger': 'automatic', 'version': '0.2.0'}
        updates.begin_automatic('0.2.0', at(0, 0))
        # Stopped before the backup: tried again.
        updates.spend(request, {'request': 'r1', 'passed': False}, moved=False)
        # Moved the checkout: the new version's start decides (confirmed, or moved back).
        updates.spend(request, {'request': 'r1', 'passed': True}, moved=True)
        # An operator's apply is not automatic mode's try.
        updates.spend({**request, 'trigger': 'console'}, {'request': 'r1', 'passed': True}, moved=False)
        updates.spend(request, None, moved=False)
        self.assertEqual(self.chosen(at(0, 10)), '0.2.0')
        # Past the backup, and the checkout never moved: never tried again automatically.
        updates.spend(request, {'request': 'r1', 'passed': True}, moved=False)
        self.assertTrue(self.entry('0.2.0')['spent'])
        self.assertIsNone(self.chosen(at(0, 50)))

    def test_the_ledger_keeps_each_version_across_writes_bounded_and_ignores_damaged_entries(self):
        updates.begin_automatic('0.2.0', at(0, 0))
        updates.mark('0.2.0', announced=True)
        self.assertEqual(updates.ledger()['versions'], {'0.2.0': {**updates.ENTRY, 'announced': True, 'tries': 1,
                                                                  'last': updates.stamp(at(0, 0))}})
        self.put('ledger.json', {'versions': {'0.2.0': {'tries': 'x', 'last': 1, 'spent': 'yes'}, '0.3.0': []}})
        self.assertEqual(updates.ledger()['versions'], {'0.2.0': updates.ENTRY})
        for number in range(updates.LEDGER_KEEP + 5):
            updates.mark(f'1.0.{number}', announced=True)
        versions = updates.ledger()['versions']
        self.assertEqual((len(versions), next(iter(versions))), (updates.LEDGER_KEEP, '1.0.5'))

    def test_a_ledger_in_the_earlier_form_is_read_into_the_per_version_one(self):
        self.put('ledger.json', {'announced': ['0.2.0'], 'attempted': ['0.3.0'], 'rolled_back': ['0.4.0', 7],
                                 'tries': {'0.5.0': {'count': 2, 'last': updates.stamp(at(0, 0)), 'ended': 'x'}}})
        self.assertEqual((self.entry('0.2.0')['announced'], self.entry('0.3.0')['spent'], self.entry('0.4.0')['rolled_back']),
                         (True, True, True))
        self.assertEqual((self.entry('0.5.0')['tries'], self.entry('0.5.0')['last']), (2, updates.stamp(at(0, 0))))


class CurrentTests(Private):
    """current.json: the supervisor's one word on what runs and what can be installed now."""

    def setUp(self):
        super().setUp()
        upstream = self.folder.parent / 'upstream'
        upstream.mkdir()
        for item in (patch.object(upgrade, 'UPSTREAM', upstream), patch.object(upgrade, 'BACKUP_LOCK', upstream / 'backup.lock'),
                     patch.object(upgrade, 'LOCK', self.folder / 'upgrade.lock')):
            item.start()
            self.addCleanup(item.stop)

    def publish(self, state=None, blockers=(), previous=None):
        return updates.publish_current(state, at(12), running=CURRENT, rollback=(False, 'x.'), blockers=blockers,
                                       previous=previous)

    def test_the_apply_verdict_has_the_shape_the_console_reads(self):
        self.assertEqual(self.publish()['apply'], None)
        self.put('available.json', document())
        record = self.publish()
        self.assertEqual(record['apply'], {'version': '0.2.0', 'tag': 'v0.2.0', 'class': 'safe', 'possible': True, 'reason': None,
                                           'acknowledgement': False})
        self.assertEqual((record['pending'], record['rollback']), (False, {'started_at': None, 'possible': False, 'reason': 'x.'}))
        self.assertEqual(self.get('current.json'), record)
        self.assertEqual(stat.S_IMODE((self.folder / 'current.json').stat().st_mode), 0o600)
        # An attended release: possible once acknowledged, and the console asks for that.
        self.put('available.json', document(release(**{'class': 'attended'})))
        self.assertEqual({key: self.publish()['apply'][key] for key in ('possible', 'reason', 'acknowledgement')},
                         {'possible': True, 'reason': None, 'acknowledgement': True})
        # Never from the console, but still named with the sentence that says why.
        for changes, sentence in (({'class': 'rebuild'}, updates.MESSAGES['class']), ({'signed': False}, updates.MESSAGES['unsigned'])):
            self.put('available.json', document(release(**changes)))
            self.assertEqual({key: self.publish()['apply'][key] for key in ('version', 'possible', 'reason')},
                             {'version': '0.2.0', 'possible': False, 'reason': sentence})
        self.put('available.json', document(refusals=['This checkout has 1 commit(s) that v0.2.0 does not contain']))
        self.assertEqual(self.publish()['apply']['reason'], updates.MESSAGES['refused'])

    def test_a_pending_upgrade_and_a_passing_blocker_make_it_impossible_for_now(self):
        self.put('available.json', document())
        record = self.publish({'phase': 'applied', 'from': 'c' * 40, 'to': COMMIT, 'started_at': 's'})
        self.assertEqual((record['pending'], record['apply']['possible'], record['apply']['reason']),
                         (True, False, updates.MESSAGES['pending']))
        with locked(upgrade.LOCK):
            blockers = updates.install_blockers(own_backup=False)
        self.assertEqual(blockers, ['Another upgrade or rollback is running.'])
        self.assertEqual(self.publish(blockers=blockers)['apply']['reason'], 'Another upgrade or rollback is running.')
        # The daily backup the supervisor runs itself is waited out, not a reason to refuse.
        with locked(upgrade.BACKUP_LOCK):
            self.assertEqual(updates.install_blockers(own_backup=True), [])
            self.assertEqual(updates.install_blockers(own_backup=False), ['A backup or restore is running; wait for it to finish.'])
        # A record still to settle is not one either: the drain settles it.
        (upgrade.UPSTREAM / 'worker-effect.json').write_text('{}')
        self.assertEqual(updates.install_blockers(own_backup=False), [])

    def test_a_check_made_on_another_commit_is_set_aside_before_anyone_reads_it(self):
        self.put('available.json', document(commit='d' * 40))
        self.assertIsNone(self.publish()['apply'])
        self.assertFalse((self.folder / 'available.json').exists())
        self.assertEqual(self.get('available.previous.json')['current']['commit'], 'd' * 40)

    def test_an_unchanged_verdict_is_not_written_again(self):
        self.put('available.json', document())
        first = self.publish()
        (self.folder / 'current.json').unlink()
        self.assertIs(updates.publish_current(None, at(13), running=CURRENT, rollback=(False, 'x.'), previous=first), first)
        self.assertFalse((self.folder / 'current.json').exists())
        self.put('available.json', document(release(signed=False)))
        self.assertFalse(updates.publish_current(None, at(13), running=CURRENT, rollback=(False, 'x.'), previous=first)['apply']['possible'])
        self.assertTrue((self.folder / 'current.json').exists())


class ZoneTests(unittest.TestCase):
    def test_the_zone_is_the_one_the_clock_uses(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'zoneinfo'
            (database / 'Asia').mkdir(parents=True)
            (database / 'Asia' / 'Dubai').write_bytes(b'TZif')
            moment = at(3)
            self.assertEqual(updates.zone(moment, {'TZ': 'Asia/Dubai'}), {'name': 'Asia/Dubai', 'offset': '+04:00'})
            self.assertEqual(updates.zone(moment, {'TZ': ':Asia/Dubai'})['name'], 'Asia/Dubai')
            # A zone name the time zone database does not know is not repeated as if it were in use.
            for configured in ('Europe/Nowhere', '../../etc/passwd', '/etc/passwd', '', "<+04>-4"):
                self.assertEqual(updates.zone(moment, {'TZ': configured})['name'], moment.tzname(), configured)
            link = Path(directory) / 'localtime'
            link.symlink_to(database / 'Asia' / 'Dubai')
            self.assertEqual(updates.zone(moment, {}, localtime=link)['name'], 'Asia/Dubai')
            utc = datetime.datetime(2026, 9, 25, 3, tzinfo=datetime.UTC)
            self.assertEqual(updates.zone(utc, {}, localtime=Path(directory) / 'missing'), {'name': 'UTC', 'offset': '+00:00'})


class Stand:
    """A child that already ended with a status, as child_status and terminate_group read it."""

    def __init__(self, status):
        self.returncode, self.pid = status, 0


class SupervisorTests(Private):
    """schedule_updates through the real Supervisor, with its children replaced by stand-ins."""

    def setUp(self):
        super().setUp()
        self.upstream = self.folder.parent / 'upstream'
        self.upstream.mkdir()
        for item in (patch.object(upgrade, 'UPSTREAM', self.upstream), patch.object(upgrade, 'BACKUP_LOCK', self.upstream / 'backup.lock'),
                     patch.object(upgrade, 'LOCK', self.folder / 'upgrade.lock')):
            item.start()
            self.addCleanup(item.stop)
        self.put('settings.json', {'check': False, 'automatic': False, 'window': {'start': '03:00', 'end': '05:00'}})
        self.supervisor = dev.Supervisor(threading.Event(), catalog=Path(self.folder) / 'absent.sqlite')
        self.supervisor.current = self.supervisor.running = CURRENT
        self.supervisor.updates_since = at(0)
        self.spawned = []
        # The child's exit status, and what it records as its outcome (lab/upgrade.py record_outcome).
        self.outcome = (0, {})

        def spawn_logged(command, log):
            self.spawned.append(command)
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text('')
            status, recorded = self.outcome
            if recorded is not None and status is not None:
                self.put('outcome.json', {'request': command[-1], 'passed': False, 'changed': status == 0, 'refusals': [],
                                          'error': None, **recorded})
            return Stand(status)
        self.supervisor.spawn_logged = spawn_logged

    def turn(self, moment=None):
        with patch('builtins.print'):
            self.supervisor.schedule_updates(moment or at(12))

    def test_an_apply_request_runs_the_verified_release_then_exits_to_restart(self):
        self.put('available.json', document())
        request = updates.create_request('apply', '0.2.0', 'console', at(12))
        # A tag in the request file (the console wrote one before) is never what runs.
        self.put('request.json', {**request, 'tag': 'v9.9.9'})
        self.turn()
        self.assertEqual(self.spawned, [['/usr/bin/python3', 'lab/upgrade.py', 'start', '--release', 'v0.2.0', '--trigger', 'console',
                                         '--request', request['id']]])
        self.assertEqual(updates.read_request()['state'], 'running')
        self.assertFalse(self.supervisor.stop_event.is_set())
        self.turn()
        self.assertIsNone(self.supervisor.update)
        self.assertIsNone(updates.read_request())
        self.assertEqual(self.get('last-request.json')['state'], 'done')
        self.assertTrue(self.supervisor.restart_for_upgrade)
        self.assertTrue(self.supervisor.stop_event.is_set())

    def test_a_refused_apply_is_failed_with_its_reason_and_nothing_restarts(self):
        self.put('available.json', document())
        self.outcome = (1, {'refusals': ['A backup or restore is running; wait for it to finish'], 'error': 'Nothing was changed'})
        updates.create_request('apply', '0.2.0', moment=at(12))
        self.turn()
        self.turn()
        last = self.get('last-request.json')
        self.assertEqual((last['state'], last['detail']),
                         ('failed', 'A backup or restore is running; wait for it to finish. Nothing was changed.'))
        self.assertFalse(self.supervisor.restart_for_upgrade)
        self.assertFalse(self.supervisor.stop_event.is_set())

    def test_past_the_request_file_the_scheduling_looks_at_most_every_fifteen_seconds(self):
        self.put('settings.json', {'check': True, 'automatic': False, 'window': {'start': '03:00', 'end': '05:00'}})
        self.put('available.json', document())
        second = datetime.timedelta(seconds=1)
        with patch.object(updates, 'check_due', return_value=False) as due:
            for offset in (0, 5, 14, 15, 20):
                self.turn(at(1) + offset * second)
        self.assertEqual(due.call_count, 2)
        # Each of those turns published the verdict, which the console reads.
        self.assertEqual(self.get('current.json')['apply']['version'], '0.2.0')
        # The request file is read on every turn.
        request = updates.create_request('check', moment=at(1))
        self.turn(at(1) + 21 * second)
        self.assertEqual(self.spawned[-1][-1], request['id'])

    def test_the_supervisor_never_trusts_the_request_file(self):
        self.put('available.json', document(release(signed=False)))
        updates.create_request('apply', '0.2.0', moment=at(12))
        self.turn()
        self.assertEqual(self.spawned, [])
        self.assertEqual(self.get('last-request.json')['detail'], updates.MESSAGES['unsigned'])

    def test_an_upgrade_and_the_daily_backup_never_overlap(self):
        self.put('available.json', document())
        updates.create_request('apply', '0.2.0', moment=at(12))
        self.supervisor.backup = subprocess.Popen(['/usr/bin/python3', '-c', 'import time; time.sleep(30)'], start_new_session=True)
        self.addCleanup(dev.terminate_group, self.supervisor.backup, 0)
        self.turn()
        self.assertEqual((self.spawned, updates.read_request()['state']), ([], 'requested'))
        dev.terminate_group(self.supervisor.backup, grace=0)
        self.supervisor.backup = None
        self.outcome = (None, None)
        self.turn()
        self.assertEqual(self.supervisor.update['kind'], 'apply')
        # While the upgrade runs, the daily backup that is due does not start.
        state = Path(self.folder) / 'state'
        state.mkdir()
        with patch.object(dev, 'STATE', state), patch.object(self.supervisor, 'spawn') as spawn:
            self.supervisor.schedule_backup(datetime.datetime(2026, 9, 25, 12, tzinfo=datetime.UTC))
        spawn.assert_not_called()
        self.assertFalse((state / 'backup-schedule.json').exists())

    def test_a_rollback_runs_only_when_the_verdict_allows_it(self):
        updates.create_request('rollback', moment=at(12))
        self.turn()
        self.assertEqual((self.spawned, self.get('last-request.json')['detail']), ([], updates.MESSAGES['no_rollback']))
        self.put('state.json', {'phase': 'confirmed', 'from': 'c' * 40, 'to': COMMIT, 'started_at': 'x',
                                'release': {'version': '0.2.0'}})
        request = updates.create_request('rollback', moment=at(12))
        with patch.object(upgrade, 'rollback_refusal', return_value=None):
            self.turn()
            self.turn()
        self.assertEqual(self.spawned, [['/usr/bin/python3', 'lab/upgrade.py', 'rollback', '--request', request['id']]])
        self.assertTrue(self.supervisor.restart_for_upgrade)
        # The ledger learns of the way back once the previous version confirms it (announce_outcome).
        self.assertFalse(self.entry('0.2.0')['rolled_back'])

    def test_an_attended_release_runs_only_with_the_operators_acknowledgement(self):
        self.put('available.json', document(release(**{'class': 'attended'})))
        request = updates.create_request('apply', '0.2.0', 'console', at(12))
        self.put('request.json', {**request, 'acknowledged': True})
        self.outcome = (None, None)
        self.turn()
        self.assertEqual(self.spawned[-1], ['/usr/bin/python3', 'lab/upgrade.py', 'start', '--release', 'v0.2.0', '--trigger', 'console',
                                            '--allow-class', 'attended', '--request', request['id']])
        self.supervisor.update = None
        # Never for automatic mode, even when its request says acknowledged.
        (self.folder / 'request.json').unlink()
        request = updates.create_request('apply', '0.2.0', 'automatic', at(12))
        self.put('request.json', {**request, 'acknowledged': True})
        self.turn()
        self.assertEqual((len(self.spawned), self.get('last-request.json')['detail']), (1, updates.MESSAGES['acknowledge']))

    def test_automatic_mode_requests_once_and_the_request_says_so(self):
        self.put('settings.json', {'check': True, 'automatic': True, 'window': {'start': '23:00', 'end': '01:00'}})
        self.put('available.json', document())
        self.put('check.json', {'attempted_at': updates.stamp(at(0)), 'error': None, 'failures': 0})
        # The try passes its point of no return, then its backup fails: it is spent.
        self.outcome = (1, {'passed': True, 'error': 'The backup before the upgrade failed; nothing was changed'})
        self.turn(at(0, 30))
        self.assertEqual(updates.read_request()['trigger'], 'automatic')
        self.assertEqual(self.entry('0.2.0')['tries'], 1)
        self.turn(at(0, 30))
        self.assertEqual(self.spawned[-1][-4:-2], ['--trigger', 'automatic'])
        self.turn(at(0, 31))
        last = self.get('last-request.json')
        self.assertEqual((last['state'], last['detail']), ('failed', 'The backup before the upgrade failed; nothing was changed.'))
        # It is never tried again automatically.
        self.turn(at(0, 50))
        self.assertIsNone(updates.read_request())
        self.assertTrue(self.entry('0.2.0')['spent'])
        self.turn(at(0, 55))
        self.assertEqual(len(self.spawned), 1)

    def test_a_drain_that_times_out_does_not_spend_the_automatic_attempt(self):
        self.put('settings.json', {'check': True, 'automatic': True, 'window': {'start': '23:00', 'end': '01:00'}})
        self.put('available.json', document())
        self.put('check.json', {'attempted_at': updates.stamp(at(0)), 'error': None, 'failures': 0})
        self.turn(at(0, 0))
        self.supervisor.sign_in = Stand(None)
        with patch.object(dev, 'DRAIN_SECONDS', 0), patch.object(self.supervisor, 'resume_work'):
            self.turn(at(0, 0))
        self.supervisor.sign_in = None
        self.assertEqual((self.spawned, self.get('last-request.json')['state']), ([], 'failed'))
        self.assertEqual((self.entry('0.2.0')['tries'], self.entry('0.2.0')['spent']), (1, False))
        self.turn(at(0, 10))
        self.assertEqual(self.entry('0.2.0')['tries'], 2)

    def test_a_network_failure_before_the_backup_does_not_spend_the_automatic_attempt(self):
        self.put('settings.json', {'check': True, 'automatic': True, 'window': {'start': '23:00', 'end': '01:00'}})
        self.put('available.json', document())
        self.put('check.json', {'attempted_at': updates.stamp(at(0)), 'error': None, 'failures': 0})
        self.outcome = (1, {'refusals': ['git fetch failed: Could not resolve host'], 'error': 'Nothing was changed'})
        for minute in (0, 1, 2, 5, 10, 11, 12, 29, 30, 31, 32, 59):
            self.turn(at(0, minute))
        # Tries at 12:00 AM, 12:10 AM and 12:30 AM, then no more; the version is not spent.
        self.assertEqual(len(self.spawned), 3)
        self.assertEqual((self.entry('0.2.0')['tries'], self.entry('0.2.0')['spent']), (3, False))

    def test_automatic_mode_waits_out_a_backup_instead_of_spending_its_attempt(self):
        self.put('settings.json', {'check': True, 'automatic': True, 'window': {'start': '23:00', 'end': '01:00'}})
        self.put('available.json', document())
        self.put('check.json', {'attempted_at': updates.stamp(at(0)), 'error': None, 'failures': 0})
        with locked(upgrade.BACKUP_LOCK):
            self.turn(at(0, 30))
        self.assertIsNone(updates.read_request())
        self.assertEqual(updates.ledger()['versions'], {})
        (self.upstream / 'worker-effect.json').write_text('{}')
        self.turn(at(0, 31))
        self.assertEqual(updates.ledger()['versions'], {})
        (self.upstream / 'worker-effect.json').unlink()
        self.turn(at(0, 32))
        self.assertEqual(self.entry('0.2.0')['tries'], 1)
        self.assertEqual(updates.read_request()['trigger'], 'automatic')

    def test_a_periodic_check_runs_the_channel_and_records_an_offline_failure(self):
        self.put('settings.json', {'check': True, 'automatic': False, 'window': {'start': '03:00', 'end': '05:00'}})
        self.put('available.json', document())
        self.turn(at(0, 4))
        self.assertEqual(self.spawned, [])
        # Offline: the channel fails and writes no result.
        self.outcome = (1, {'error': 'The release source could not be read: offline'})
        self.turn(at(0, 5))
        self.assertEqual(self.spawned[0][:-1], ['/usr/bin/python3', 'lab/upgrade.py', 'channel', '--json', '--request'])
        self.turn(at(0, 6))
        self.assertEqual(self.get('check.json')['failures'], 1)
        self.assertEqual(self.get('check.json')['error'], 'The release source could not be read: offline.')
        self.assertEqual(self.get('available.json')['available']['version'], '0.2.0')
        self.turn(at(0, 7))
        self.assertEqual(len(self.spawned), 1)


class NotificationTests(ProducerCase):
    def setUp(self):
        super().setUp()
        folder = self.directory / 'upgrades'
        item = patch.object(updates, 'UPGRADES', folder)
        item.start()
        self.addCleanup(item.stop)

    def details(self):
        return [json.loads(row[0]) for row in self.rows('SELECT detail FROM notification_outbox ORDER BY rowid')]

    def test_an_available_release_is_announced_once_per_version(self):
        self.assertIsNotNone(updates.announce_available(document(), catalog=self.catalog))
        self.assertIsNone(updates.announce_available(document(), catalog=self.catalog))
        self.assertEqual(self.outbox(), [('update.available', 'info', 'update_available', None)])
        self.assertEqual(self.details(), [{'class': 'safe', 'version': '0.2.0'}])

    def test_a_newer_signed_release_passed_over_is_announced_and_an_unsigned_one_is_not(self):
        checked = {**document(), 'newest': {'version': '0.4.0', 'tag': 'v0.4.0', 'class': 'manual', 'signed': True, 'reasons': ['x']}}
        self.assertIsNotNone(updates.announce_available(checked, catalog=self.catalog))
        self.assertEqual(self.details(), [{'class': 'safe', 'version': '0.2.0'}, {'class': 'manual', 'version': '0.4.0'}])
        self.assertIsNone(updates.announce_available(checked, catalog=self.catalog))
        quiet = {**document(available=False), 'available': None,
                 'newest': {'version': '0.5.0', 'tag': 'v0.5.0', 'class': 'safe', 'signed': False, 'reasons': ['x']}}
        self.assertIsNone(updates.announce_available(quiet, catalog=self.catalog))
        self.assertEqual(len(self.details()), 2)

    def test_an_announcement_the_catalog_did_not_take_is_tried_again(self):
        document_ = document(release(version='0.3.0', tag='v0.3.0'))
        self.assertIsNone(updates.announce_available(document_, catalog=self.directory / 'absent.sqlite'))
        self.assertNotIn('0.3.0', updates.ledger()['versions'])
        self.assertIsNotNone(updates.announce_available(document_, catalog=self.catalog))

    def test_each_outcome_earns_its_event_and_every_way_back_is_remembered(self):
        base = {'from': 'c' * 40, 'to': 'b' * 40, 'started_at': '2026-09-25T03:00:00+00:00', 'trigger': 'automatic',
                'release': {'version': '0.2.0'}}
        applied = {**base, 'phase': 'applied'}
        self.assertEqual(updates.announce_outcome(applied, {**base, 'phase': 'confirmed'}, catalog=self.catalog), 'update.applied')
        self.assertEqual(updates.announce_outcome(applied, {**base, 'phase': 'rolling_back', 'automatic': True},
                                                  catalog=self.catalog), 'update.rolled_back')
        self.assertEqual(updates.announce_outcome({**base, 'phase': 'rolling_back'}, {**base, 'phase': 'rollback_failed'},
                                                  catalog=self.catalog), 'update.rollback_failed')
        # A confirmation of the way back, or no change at all, earns nothing more.
        self.assertIsNone(updates.announce_outcome({**base, 'phase': 'rolling_back'}, {**base, 'phase': 'rolled_back'}, catalog=self.catalog))
        self.assertIsNone(updates.announce_outcome(applied, applied, catalog=self.catalog))
        self.assertEqual(self.outbox(), [('update.applied', 'info', 'update_applied', None),
                                         ('update.rolled_back', 'warning', 'update_rolled_back', None),
                                         ('update.rollback_failed', 'critical', 'update_rollback_failed', None)])
        self.assertEqual(self.details()[0], {'trigger': 'automatic', 'version': '0.2.0'})
        self.assertTrue(updates.ledger()['versions']['0.2.0']['rolled_back'])

    def test_a_way_back_for_a_record_the_previous_version_had_to_settle_leaves_the_version_open(self):
        base = {'from': 'c' * 40, 'to': 'b' * 40, 'started_at': '2026-09-25T03:00:00+00:00', 'trigger': 'automatic',
                'release': {'version': '0.3.0'}, 'retryable': True}
        self.assertEqual(updates.announce_outcome({**base, 'phase': 'applied'}, {**base, 'phase': 'rolling_back', 'automatic': True},
                                                  catalog=self.catalog), 'update.rolled_back')
        self.assertNotIn('0.3.0', updates.ledger()['versions'])

    def test_the_supervisor_emits_the_outcome_of_a_start(self):
        with patch.object(upgrade, 'load_state', side_effect=[{'phase': 'applied', 'from': 'c' * 40, 'to': 'b' * 40, 'started_at': 's'},
                                                              {'phase': 'confirmed', 'from': 'c' * 40, 'to': 'b' * 40, 'started_at': 's'}]), \
                patch.object(upgrade, 'after_start', return_value=False):
            self.assertFalse(dev.upgrade_outcome(True, catalog=self.catalog))
        self.assertEqual(self.outbox(), [('update.applied', 'info', 'update_applied', None)])
        self.assertEqual(self.details(), [{'trigger': 'cli', 'version': 'bbbbbbbbbbbb'}])


class RestartTests(ProducerCase):
    """dev.main exits with RESTART_FOR_UPGRADE once the owned runtime stopped."""

    def test_the_exit_code_comes_after_the_runtime_stopped(self):
        state = self.directory / 'state'
        state.mkdir()
        stages = []

        def run_stage(command, stop_event, timeout=180, pass_fds=(), env=None):
            stages.append(command[-1])
            return 0

        class Moved:
            restart_for_upgrade = True

            def __init__(self, stop_event, worker_fd, catalog=None):
                pass

            def run(self):
                stages.append('supervisor')
        with patch.object(dev, 'STATE', state), patch.object(notification_producers, 'CATALOG', self.catalog), \
                patch.object(dev, 'run_stage', run_stage), patch.object(dev, 'upgrade_prepare', return_value=False), \
                patch.object(dev, 'settle_leftover'), \
                patch.object(dev, 'upgrade_outcome', return_value=False), patch.object(dev, 'upgrade_notices'), \
                patch.object(dev.console_build_check, 'is_fresh', return_value=(True, 'fresh')), \
                patch.object(dev, 'Supervisor', Moved), patch.object(dev.os, 'chdir'), patch.object(dev.sys, 'argv', ['dev.py']), \
                patch('builtins.print'):
            with self.assertRaises(SystemExit) as raised:
                dev.main()
        self.assertEqual(raised.exception.code, dev.RESTART_FOR_UPGRADE)
        self.assertNotEqual(dev.RESTART_FOR_UPGRADE, 0)
        self.assertEqual(stages[-2:], ['supervisor', 'stop'])
        unit = (ROOT / 'deploy' / 'sbarbase.service').read_text()
        self.assertIn(f'RestartForceExitStatus={dev.RESTART_FOR_UPGRADE}', unit)


class RollbackVerdictTests(Checkout):
    """upgrade.rollback_refusal is the one answer for `rollback`, `rollback --check` and the console."""

    def test_the_verdict_follows_the_upgrade_and_the_catalog(self):
        # upgrade.main changes into its checkout; come back before the directory goes.
        self.addCleanup(os.chdir, os.getcwd())
        self.catalog = self.upstream / 'control.sqlite'
        store(self.catalog, 2, 3)
        self.repo.git('checkout', '-q', '--detach', self.first)
        base = self.repo.commit('base', {'src/control/catalog.ts': 'export const CATALOG_SCHEMA_VERSION = 2;\n'})
        target = self.repo.commit('target', {'src/control/catalog.ts': 'export const CATALOG_SCHEMA_VERSION=3;\n'})
        self.repo.git('checkout', '-q', '--detach', base)
        self.assertEqual(updates.rollback_verdict(upgrade.load_state()), (False, updates.MESSAGES['no_rollback']))
        with patch('builtins.print'):
            upgrade.start(target, trigger='console')
        self.assertEqual(upgrade.load_state()['trigger'], 'console')
        self.assertFalse(updates.rollback_verdict(upgrade.load_state())[0])
        upgrade.before_start()
        store(self.catalog, 3, 9)
        upgrade.after_start(True)
        possible, reason = updates.rollback_verdict(upgrade.load_state())
        self.assertFalse(possible)
        self.assertIn('forward only', reason)
        with patch('builtins.print') as shown:
            self.assertEqual(upgrade.main(['rollback', '--check']), 1)
        self.assertIn('forward only', shown.call_args.args[0])
        store(self.catalog, 2, 9)
        self.assertEqual(updates.rollback_verdict(upgrade.load_state()), (True, None))
        with patch('builtins.print'):
            self.assertEqual(upgrade.main(['rollback', '--check']), 0)
        self.assertEqual(self.head(), target)
        # A local edit to a tracked file: the console is told why, in a sentence, before anything moves.
        (self.repo.root / 'src/control/catalog.ts').write_text('edited on the server\n')
        possible, reason = updates.rollback_verdict(upgrade.load_state())
        self.assertFalse(possible)
        self.assertEqual(reason, 'The checkout has local changes to tracked files (src/control/catalog.ts), which a '
                                 'rollback would move aside. Commit or discard them on the server first. Nothing was changed.')


if __name__ == '__main__':
    unittest.main()
