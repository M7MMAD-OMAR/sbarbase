"""The Edge Functions container's lock down: the flags, who gets them, how the user is chosen, and what a kept container must carry.
No Docker: the launch arguments are read from a patched docker call. The live measurements are in
docs/evidence/functions-container-hardening-2026-10-30/results.json."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import durable_runtime as runtime
import resource_policy

E = 'e_' + 'a' * 24
NETWORK = runtime.functions_network(E)
# launch_spec digests made by HEAD's launch_spec before the lock down existed, with the block device fixed to 8:0: a component that is not locked down must keep its label,
# or a planned upgrade restart would find every kept container drifted.
AUTH_SPEC = '990f03a71972ab8e9a3c77d138a5df87'
DB_SPEC = 'b1c22e8ed7822f0e490da87666ac6fe2'
# The Functions label, as launch_spec makes it with the command that starts the event worker. It changed on 2026-10-30 (F3) on purpose: a
# kept Functions container started with the earlier command is recreated at the next start, once. The label before: dc843ba8f798838899e5a15036e13a69.
FUNCTIONS_SPEC_BEFORE = '2316ac043a7d3d739b22e273fb5b6eb8'
LOCKED_DOWN = ('--user', '--cap-drop', '--security-opt', '--read-only', '--tmpfs')


def flags(uid=1000, gid=1000):
    return resource_policy.hardening_flags('production.functions', uid, gid)


class FlagTests(unittest.TestCase):
    def test_functions_get_every_flag_with_sized_tmpfs_for_tmp_and_the_deno_cache(self):
        args = flags(1234, 5678)
        self.assertEqual(args[args.index('--user') + 1], '1234:5678')
        self.assertEqual(args[args.index('--cap-drop') + 1], 'ALL')
        self.assertEqual(args[args.index('--security-opt') + 1], 'no-new-privileges')
        self.assertIn('--read-only', args)
        tmpfs = [args[i + 1] for i, arg in enumerate(args) if arg == '--tmpfs']
        self.assertEqual(tmpfs[0], '/tmp:rw,nosuid,nodev,noexec,size=32m')
        self.assertEqual(tmpfs[1], '/deno-dir:rw,nosuid,nodev,noexec,size=32m,mode=0700,uid=1234,gid=5678')

    def test_no_other_tier_gets_any_of_them(self):
        for tier in resource_policy.TIERS:
            if tier != 'production.functions':
                self.assertEqual(resource_policy.hardening_flags(tier, 1000, 1000), (), tier)
                self.assertEqual(resource_policy.hardening_flags(tier), (), tier)

    def test_a_hardened_tier_refuses_root_and_a_missing_user(self):
        for uid, gid in ((0, 0), (0, 1000), (1000, 0), (None, None), (1000, None), (True, 1)):
            with self.assertRaises(resource_policy.ResourcePolicyError, msg=(uid, gid)):
                resource_policy.hardening_flags('production.functions', uid, gid)
        with self.assertRaises(resource_policy.ResourcePolicyError):
            resource_policy.hardening_flags('production.functions')

    def test_the_tmpfs_sizes_leave_room_for_the_runtime_and_one_worker(self):
        # Measured: about 155 MiB anonymous with four warm functions, one worker up to 150 MiB, tmpfs never reclaimed, 384 MiB limit.
        # 64m + 96m was OOM killed with one 130 MiB worker call; 32m + 32m peaked at 365 MiB. No more than that.
        sizes = [int(size.rstrip('m')) for size in (resource_policy.HARDENED_TMP_SIZE, resource_policy.HARDENED_CACHE_SIZE)]
        self.assertLessEqual(sum(sizes), 64)
        self.assertLess(155 + sum(sizes) + 150, 384)

    def test_the_cache_directory_the_runtime_is_told_about_is_the_one_that_is_writable(self):
        runtime_self = object.__new__(runtime.Runtime)
        config = runtime_self.functions_configuration(E, {'jwt': 'j' * 64}, '172.30.4.1')
        self.assertEqual(config['DENO_DIR'], resource_policy.HARDENED_CACHE_DIR)
        self.assertIn(resource_policy.HARDENED_CACHE_DIR + ':', ' '.join(flags()))


class SpecTests(unittest.TestCase):
    def setUp(self):
        device = patch.object(resource_policy, 'device', return_value='8:0')
        device.start()
        self.addCleanup(device.stop)

    def spec(self, **kw):
        return runtime.launch_spec('functions', 'ref', '384m', .5, 'production.functions', NETWORK, binds=[('/a', '/b')],
                                   command=runtime.FUNCTIONS_COMMAND, **kw)

    def test_components_that_are_not_locked_down_keep_their_earlier_label(self):
        self.assertEqual(runtime.launch_spec('auth', 'ref@sha256:aa', '256m', .25, 'production', 'sbarbase-durable-net'), AUTH_SPEC)
        self.assertEqual(runtime.launch_spec('db', 'ref@sha256:bb', '1024m', 1, 'system.db', 'sbarbase-durable-net', volumes=[('vol', '/data')]), DB_SPEC)
        # Passing no flags is the same call as before the change.
        self.assertEqual(runtime.launch_spec('auth', 'ref@sha256:aa', '256m', .25, 'production', 'sbarbase-durable-net', hardening=()), AUTH_SPEC)

    def test_the_spec_differs_with_and_without_the_flags_and_with_each_user(self):
        self.assertEqual(runtime.launch_spec('functions', 'ref@sha256:cc', '384m', .5, 'production.functions', 'sbarbase-durable-e_x-fn',
                                             binds=[('/a', '/b')], command=runtime.FUNCTIONS_COMMAND), FUNCTIONS_SPEC_BEFORE)
        without, locked, other = self.spec(), self.spec(hardening=flags()), self.spec(hardening=flags(2000, 2000))
        self.assertEqual(len({without, locked, other}), 3)
        self.assertEqual(locked, self.spec(hardening=flags()))

    def test_every_flag_is_part_of_the_spec(self):
        base = flags()
        for index in range(len(base)):
            changed = tuple('x' if i == index else value for i, value in enumerate(base))
            self.assertNotEqual(self.spec(hardening=changed), self.spec(hardening=base), index)


class LaunchTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.runtime = object.__new__(runtime.Runtime)
        self.runtime.pins = {name: {'id': 'sha256:' + 'a' * 64} for name in ('x', 'db', 'auth', 'rest', 'storage', 'studio', 'meta')}
        self.calls = []
        digest = 'sha256:' + 'a' * 64
        self.reference = 'docker.io/supabase/x@' + digest
        for item in [patch.object(self.runtime, 'image', return_value=(self.reference, digest)),
                     patch.object(runtime, 'inspect', return_value=None), patch.object(runtime, 'join'),
                     patch.object(runtime, 'PRIVATE', Path(directory.name)), patch.object(runtime.lab, 'secure_file'),
                     patch.object(runtime.resource_policy, 'device', return_value='8:0'),
                     patch.object(runtime.lab, 'docker', side_effect=lambda *a, **k: self.calls.append(a) or SimpleNamespace(stdout='cid', returncode=0))]:
            item.start()
            self.addCleanup(item.stop)

    def run_args(self, component, tier, memory, cpus, **kw):
        self.calls.clear()
        self.runtime.launch('c', component, {}, memory, cpus, tier=tier, **kw)
        return next(args for args in self.calls if args[0] == 'run')

    def test_the_functions_container_is_created_with_the_flags_before_the_image(self):
        args = self.run_args('x', 'production.functions', '384m', .5, command=runtime.FUNCTIONS_COMMAND, hardening=flags(1000, 1000))
        for flag in LOCKED_DOWN:
            self.assertIn(flag, args)
        self.assertEqual(args[args.index('--user') + 1], '1000:1000')
        self.assertLess(args.index('--read-only'), args.index(self.reference))
        self.assertEqual(args[args.index(self.reference):], (self.reference, *runtime.FUNCTIONS_COMMAND))
        # The recorded spec is the one a later start compares with.
        specs = [value for value in args if str(value).startswith('io.sbarbase.spec=')]
        self.assertEqual(specs, ['io.sbarbase.spec=' + runtime.launch_spec('x', self.reference, '384m', .5, 'production.functions', runtime.NETWORK,
                                                                           command=runtime.FUNCTIONS_COMMAND, hardening=flags(1000, 1000))])

    def test_db_auth_rest_storage_and_studio_get_none_of_them(self):
        for component, tier, memory, cpus in (('db', 'system.db', '1024m', 1), ('auth', 'production', '256m', .25), ('rest', 'production', '256m', .25),
                                              ('storage', 'system.storage', '512m', .5), ('studio', 'operator.studio', '512m', .5),
                                              ('meta', 'operator.meta', '256m', .25)):
            args = self.run_args(component, tier, memory, cpus)
            for flag in LOCKED_DOWN:
                self.assertNotIn(flag, args, component)

    def test_a_functions_container_is_never_created_without_the_lock_down(self):
        with self.assertRaises(RuntimeError):
            self.run_args('x', 'production.functions', '384m', .5, command=runtime.FUNCTIONS_COMMAND)
        self.assertFalse([args for args in self.calls if args[0] == 'run'])


class UserTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        patcher = patch.object(runtime, 'PRIVATE', self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        runtime.functions_private(E).mkdir(parents=True)

    def owned_by(self, uid, gid):
        # Only the private directory's owner is faked; every other stat (mkdir, is_dir) is the real one.
        real = runtime.os.stat

        def stat(path, *a, **k):
            result = real(path, *a, **k)
            if Path(path) != runtime.functions_private(E):
                return result
            return SimpleNamespace(**{**{name: getattr(result, name) for name in dir(result) if name.startswith('st_')}, 'st_uid': uid, 'st_gid': gid})
        fake = Mock(side_effect=stat)
        return patch.object(runtime.os, 'stat', fake)

    def test_the_user_is_the_owner_of_the_private_directory_not_a_fixed_number(self):
        for uid, gid in ((1000, 1000), (4242, 4343), (1500, 100)):
            with self.owned_by(uid, gid) as stat:
                self.assertEqual(runtime.functions_user(E), (uid, gid))
                self.assertEqual(stat.call_args[0][0], runtime.functions_private(E))
                self.assertEqual(runtime.functions_hardening(E)[1], f'{uid}:{gid}')

    def test_it_reads_the_real_owner_of_a_real_directory(self):
        status = runtime.functions_private(E).stat()
        if not status.st_uid:
            self.skipTest('the tests run as root')
        self.assertEqual(runtime.functions_user(E), (status.st_uid, status.st_gid or status.st_uid))

    def test_a_group_of_root_falls_back_to_the_users_own_number(self):
        with self.owned_by(4242, 0):
            self.assertEqual(runtime.functions_user(E), (4242, 4242))

    def test_a_private_directory_owned_by_root_is_refused_not_run_as_root(self):
        with self.owned_by(0, 0):
            with self.assertRaises(RuntimeError) as caught:
                runtime.functions_user(E)
            self.assertIn('unprivileged', str(caught.exception))

    def test_start_stops_before_any_container_when_the_owner_is_root(self):
        instance = object.__new__(runtime.Runtime)
        instance.values = {'environments': {E: {'jwt': 'j' * 64}}}
        instance.pins = {'functions': {'id': 'sha256:fn'}}
        docker = Mock(return_value=SimpleNamespace(stdout='', returncode=0))
        with self.owned_by(0, 0), patch.object(runtime, 'STATE', self.root / 'state'), patch.object(runtime, 'inspect', return_value=None), \
                patch.object(runtime, 'functions_address', return_value=('172.30.4.1', '172.30.4.0/24')), patch.object(runtime, 'ensure_egress'), \
                patch.object(runtime.lab, 'secure_file'), patch.object(runtime.lab, 'docker', docker), patch.object(instance, 'launch') as launch:
            with self.assertRaises(RuntimeError):
                instance.functions_start(E)
        launch.assert_not_called()


class StartTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.instance = object.__new__(runtime.Runtime)
        self.instance.values = {'environments': {E: {'jwt': 'j' * 64}}}
        self.instance.pins = {'functions': {'id': 'sha256:fn'}}
        self.exec_code = 0
        self.calls = []

        def docker(*args, **kw):
            self.calls.append(args)
            return SimpleNamespace(stdout='', returncode=self.exec_code if args[0] == 'exec' else 0)
        for item in [patch.object(runtime, 'STATE', self.root / 'state'), patch.object(runtime, 'PRIVATE', self.root / 'private'),
                     patch.object(runtime, 'inspect', return_value=None), patch.object(runtime, 'ensure_egress'),
                     patch.object(runtime, 'functions_user', return_value=(4242, 4343)),
                     patch.object(runtime, 'functions_address', return_value=('172.30.4.1', '172.30.4.0/24')),
                     patch.object(runtime.lab, 'docker', side_effect=docker), patch.object(runtime.lab, 'secure_file', side_effect=lambda p, t: Path(p).write_text(t)),
                     patch.object(self.instance, 'endpoint', return_value='http://10.0.0.9:9000'), patch.object(runtime, 'http', return_value=(404, b'')),
                     patch.object(runtime.time, 'sleep')]:
            item.start()
            self.addCleanup(item.stop)

    def test_start_passes_the_owners_flags_and_then_checks_that_the_user_can_read_its_mounts(self):
        with patch.object(self.instance, 'launch') as launch:
            self.instance.functions_start(E)
        hardening = launch.call_args.kwargs['hardening']
        self.assertEqual(hardening, flags(4242, 4343))
        checks = [args for args in self.calls if args[0] == 'exec']
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0][1], f'sbarbase-durable-{E}-functions')
        for path in ('/run/sbarbase/secrets.json', '/home/deno/main/index.ts', '/home/deno/functions'):
            self.assertIn(path, checks[0][-1])

    def test_a_user_that_cannot_read_its_secrets_fails_the_start_and_the_container_is_removed(self):
        self.exec_code = 1
        removed = []
        # The container exists once launch ran, so retire finds it and removes it: the next start must not keep it.
        with patch.object(self.instance, 'launch'), patch.object(runtime, 'retire', side_effect=removed.append):
            with self.assertRaises(RuntimeError) as caught:
                self.instance.functions_start(E)
        self.assertEqual(removed, [f'sbarbase-durable-{E}-functions'])
        self.assertIn('4242:4343', str(caught.exception))


class KeptTests(unittest.TestCase):
    """functions_kept with the label launch writes. The fixture follows test_kept_runtime.FunctionsTests."""

    def setUp(self):
        device = patch.object(resource_policy, 'device', return_value='8:0')
        device.start()
        self.addCleanup(device.stop)
        self.instance = object.__new__(runtime.Runtime)
        self.instance.values = {'environments': {E: {'jwt': 'j' * 64}}}
        self.instance.kept = {'from': 'b' * 40, 'containers': {'id-fn': {'image': 'sha256:fn', 'name': f'sbarbase-durable-{E}-functions'}}}
        self.instance.pins = {'functions': {'id': 'sha256:fn'}}
        self.user = patch.object(runtime, 'functions_user', return_value=(1000, 1000))
        self.user.start()
        self.addCleanup(self.user.stop)

    def host(self, hardening=None):
        """HostConfig and Config.User as docker inspect reports a container made with these flags."""
        args = list(hardening or flags(1000, 1000))
        tmpfs = {value.split(':', 1)[0]: value.split(':', 1)[1] for flag, value in zip(args, args[1:]) if flag == '--tmpfs'}
        return {'Privileged': False, 'ReadonlyRootfs': True, 'CapDrop': ['ALL'], 'CapAdd': None, 'SecurityOpt': ['no-new-privileges'], 'Tmpfs': tmpfs}, args[args.index('--user') + 1]

    def container(self, spec, host=None, user=None):
        key = lambda role: runtime.service_token('j' * 64, {'role': role, 'iss': 'sbarbase'}, 10 * 365 * 24 * 3600)
        env = {'DENO_DIR': resource_policy.HARDENED_CACHE_DIR, 'SB_JWT_SECRET': 'j' * 64, 'SB_STORAGE_URL': 'http://172.30.4.1:25433',
               'SUPABASE_ANON_KEY': key('anon'), 'SUPABASE_SERVICE_ROLE_KEY': key('service_role')}
        return {'Id': 'id-fn', 'Image': 'sha256:fn', 'Name': f'/sbarbase-durable-{E}-functions', 'State': {'Running': True},
                'Config': {'Env': [f'{k}={v}' for k, v in env.items()], 'Labels': {'io.sbarbase.spec': spec}, 'User': self.host()[1] if user is None else user},
                'HostConfig': host if host is not None else self.host()[0],
                'Mounts': [{'Source': str(source), 'Destination': target, 'RW': False} for source, target in runtime.functions_binds(E)],
                'NetworkSettings': {'Networks': {NETWORK: {}, runtime.EGRESS: {}}}}

    def kept(self, item):
        wanted = {'DENO_DIR': resource_policy.HARDENED_CACHE_DIR, 'SB_JWT_SECRET': 'j' * 64, 'SB_STORAGE_URL': 'http://172.30.4.1:25433',
                  'SUPABASE_ANON_KEY': 'fresh', 'SUPABASE_SERVICE_ROLE_KEY': 'fresh'}
        with patch.object(runtime, 'inspect', return_value={'Internal': True}), \
                patch.object(runtime, 'functions_address', return_value=('172.30.4.1', '172.30.4.0/24')), \
                patch.object(self.instance, 'functions_configuration', return_value=wanted), \
                patch.object(self.instance, 'image', return_value=('ref', 'sha256:fn')), \
                patch.object(runtime.subprocess, 'run', return_value=SimpleNamespace(returncode=0)), \
                patch.object(runtime.lab, 'docker', return_value=SimpleNamespace(stdout='[]', returncode=0)):
            return self.instance.functions_kept(E, self.instance.values['environments'][E], item, NETWORK)

    def spec(self, **kw):
        return runtime.launch_spec('functions', 'ref', '384m', .5, 'production.functions', NETWORK, binds=runtime.functions_binds(E),
                                   command=runtime.FUNCTIONS_COMMAND, **kw)

    def test_a_container_made_with_the_flags_is_kept(self):
        self.assertTrue(self.kept(self.container(self.spec(hardening=flags(1000, 1000)))))

    def test_a_container_made_before_the_flags_existed_is_replaced(self):
        self.assertFalse(self.kept(self.container(self.spec())))

    def test_a_container_made_for_another_user_is_replaced(self):
        self.assertFalse(self.kept(self.container(self.spec(hardening=flags(2000, 2000)))))

    def test_a_container_whose_label_is_right_but_whose_flags_are_not_is_replaced(self):
        right = self.spec(hardening=flags(1000, 1000))
        base, user = self.host()
        self.assertTrue(self.kept(self.container(right, host=dict(base), user=user)))
        doctored = {
            'privileged': {'Privileged': True},
            'writable root': {'ReadonlyRootfs': False},
            'no cap drop': {'CapDrop': None},
            'another cap drop': {'CapDrop': ['NET_RAW']},
            'a capability added': {'CapAdd': ['SYS_ADMIN']},
            'privileges may grow': {'SecurityOpt': []},
            'other security option': {'SecurityOpt': ['seccomp=unconfined']},
            'seccomp off besides': {'SecurityOpt': ['no-new-privileges', 'seccomp=unconfined']},
            'apparmor off besides': {'SecurityOpt': ['no-new-privileges', 'apparmor=unconfined']},
            'label off besides': {'SecurityOpt': ['no-new-privileges', 'label=disable']},
            'all profiles off besides': {'SecurityOpt': ['no-new-privileges', 'seccomp=unconfined', 'apparmor=unconfined', 'label=disable']},
            'a different spelling': {'SecurityOpt': ['no-new-privileges:true']},
            'a tmpfs missing': {'Tmpfs': {'/tmp': base['Tmpfs']['/tmp']}},
            'no tmpfs': {'Tmpfs': None},
            'a bigger tmp': {'Tmpfs': {**base['Tmpfs'], '/tmp': 'rw,nosuid,nodev,noexec,size=1g'}},
            'an executable cache': {'Tmpfs': {**base['Tmpfs'], '/deno-dir': base['Tmpfs']['/deno-dir'].replace('noexec', 'exec')}},
            'an extra tmpfs': {'Tmpfs': {**base['Tmpfs'], '/var': 'rw,size=1g'}},
        }
        for label, change in doctored.items():
            with self.subTest(label):
                self.assertFalse(self.kept(self.container(right, host={**base, **change}, user=user)))
        for label, who in (('root', 'root'), ('no user', ''), ('another user', '2000:2000'), ('uid only', '1000')):
            with self.subTest('user ' + label):
                self.assertFalse(self.kept(self.container(right, host=dict(base), user=who)))
        with self.subTest('no HostConfig at all'):
            item = self.container(right)
            del item['HostConfig']
            self.assertFalse(self.kept(item))

    def test_tmpfs_options_are_compared_whatever_their_order(self):
        base, user = self.host()
        shuffled = {path: ','.join(reversed(options.split(','))) for path, options in base['Tmpfs'].items()}
        self.assertTrue(self.kept(self.container(self.spec(hardening=flags(1000, 1000)), host={**base, 'Tmpfs': shuffled}, user=user)))

    def test_a_private_directory_that_is_now_root_owned_is_not_kept(self):
        with patch.object(runtime, 'functions_user', side_effect=RuntimeError('root')):
            self.assertFalse(self.kept(self.container(self.spec(hardening=flags(1000, 1000)))))


class ReadableCheckTests(unittest.TestCase):
    """The check run inside the container, and when and how it is run."""

    def tree(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        for relative in ('run/secrets.json', 'main/index.ts', 'event/index.ts', 'functions/functions.json', 'functions/v1/hello/index.ts', 'functions/v1/_shared/lib/util.ts'):
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_text('x')
        return root

    def run_check(self, root):
        script = (runtime.FUNCTIONS_READABLE.replace('/run/sbarbase', str(root / 'run')).replace('/home/deno/functions', str(root / 'functions'))
                  .replace('/home/deno/main', str(root / 'main')).replace('/home/deno/event', str(root / 'event')))
        return subprocess.run(['sh', '-c', script], capture_output=True, timeout=30).returncode

    def test_the_script_passes_a_readable_tree_and_fails_on_any_unreadable_entry_at_any_depth(self):
        if os.geteuid() == 0:
            self.skipTest('root reads everything')
        root = self.tree()
        self.assertEqual(self.run_check(root), 0)
        for relative, directory in (('run/secrets.json', False), ('main/index.ts', False), ('event/index.ts', False), ('functions/functions.json', False), ('functions/v1/hello/index.ts', False),
                                    ('functions/v1/_shared/lib/util.ts', False), ('functions/v1/_shared/lib', True), ('functions/v1/hello', True)):
            target = root / relative
            mode = target.stat().st_mode
            target.chmod(0o000)
            try:
                self.assertNotEqual(self.run_check(root), 0, relative)
            finally:
                target.chmod(mode)
            self.assertEqual(self.run_check(root), 0, relative)

    def test_a_directory_that_can_be_read_but_not_entered_fails_the_check(self):
        if os.geteuid() == 0:
            self.skipTest('root reads everything')
        root = self.tree()
        for relative in ('functions/v1', 'functions/v1/hello', 'functions/v1/_shared/lib', 'main', 'event'):
            target = root / relative
            mode = target.stat().st_mode
            target.chmod(0o600)
            try:
                self.assertNotEqual(self.run_check(root), 0, relative)
            finally:
                target.chmod(mode)
            self.assertEqual(self.run_check(root), 0, relative)

    def test_an_unreadable_file_deep_in_the_tree_is_found(self):
        if os.geteuid() == 0:
            self.skipTest('root reads everything')
        root = self.tree()
        deep = root / 'functions' / 'v1' / 'hello' / 'a' / 'b' / 'c' / 'd' / 'e' / 'f' / 'g' / 'h'
        deep.mkdir(parents=True)
        (deep / 'x.ts').write_text('x')
        self.assertEqual(self.run_check(root), 0)
        (deep / 'x.ts').chmod(0o000)
        self.assertNotEqual(self.run_check(root), 0)

    def test_the_scan_stops_at_the_first_finding_and_has_no_depth_cap(self):
        self.assertIn('-quit', runtime.FUNCTIONS_READABLE)
        self.assertNotIn('-maxdepth', runtime.FUNCTIONS_READABLE)

    def setUp(self):
        self.instance = object.__new__(runtime.Runtime)
        self.calls = []

    def docker(self, code=0, error=None):
        def run(*args, **kw):
            self.calls.append((args, kw))
            if error:
                raise error
            return SimpleNamespace(stdout='', returncode=code)
        return patch.object(runtime.lab, 'docker', side_effect=run)

    def test_the_exec_has_a_timeout(self):
        with self.docker(), patch.object(runtime, 'retire') as retire:
            self.instance.functions_readable('c', '1000:1000')
        self.assertEqual(self.calls[0][1]['timeout'], runtime.FUNCTIONS_READABLE_SECONDS)
        retire.assert_not_called()

    def test_an_exec_that_hangs_or_fails_to_run_fails_closed_and_removes_the_container(self):
        for error in (RuntimeError('Docker operation exec timed out after 30s'),):
            with self.docker(error=error), patch.object(runtime, 'retire') as retire:
                with self.assertRaises(RuntimeError):
                    self.instance.functions_readable('c', '1000:1000')
            retire.assert_called_once_with('c')

    def test_a_kept_container_is_checked_too_and_removed_when_it_fails(self):
        for code, expect_failure in ((0, False), (1, True)):
            self.calls.clear()
            instance = object.__new__(runtime.Runtime)
            instance.values = {'environments': {E: {'jwt': 'j' * 64}}}
            instance.pins = {'functions': {'id': 'sha256:fn'}}
            directory = tempfile.TemporaryDirectory()
            self.addCleanup(directory.cleanup)
            root = Path(directory.name)
            with self.docker(code=code), patch.object(runtime, 'STATE', root / 'state'), patch.object(runtime, 'PRIVATE', root / 'private'), \
                    patch.object(runtime, 'functions_user', return_value=(1000, 1000)), patch.object(runtime, 'ensure_egress'), \
                    patch.object(runtime, 'functions_address', return_value=('172.30.4.1', '172.30.4.0/24')), \
                    patch.object(runtime, 'inspect', side_effect=lambda kind, name: {'Id': 'kept-id', 'NetworkSettings': {'Networks': {}}} if kind == 'container' else {'Internal': True, 'Labels': {'io.sbarbase.functions': E}}), patch.object(instance, 'functions_kept', return_value=True), \
                    patch.object(runtime, 'retire') as retire, patch.object(instance, 'launch') as launch, patch.object(runtime.lab, 'secure_file'), patch.object(runtime, 'join'), \
                    patch.object(instance, 'endpoint', return_value='http://10.0.0.9:9000'), patch.object(runtime, 'http', return_value=(404, b'')):
                if expect_failure:
                    with self.assertRaises(RuntimeError):
                        instance.functions_start(E)
                    retire.assert_called_with(f'sbarbase-durable-{E}-functions')
                else:
                    instance.functions_start(E)
                    retire.assert_not_called()
            launch.assert_not_called()
            execs = [args for args, _ in self.calls if args[0] == 'exec']
            self.assertEqual(len(execs), 1, code)


class NoLongDashTests(unittest.TestCase):
    def test_the_sources_hold_no_em_or_en_dash(self):
        for name in ('resource_policy.py', 'durable_runtime.py', 'test_functions_container.py'):
            text = (Path(__file__).parent / name).read_text()
            self.assertFalse({chr(0x2014), chr(0x2013)} & set(text), name)


if __name__ == '__main__':
    unittest.main()


class EventWorkerTests(unittest.TestCase):
    """The runtime starts with an event worker, mounted read only from this repository (F3, lab/functions/event-worker)."""

    def test_the_command_starts_the_event_worker_from_its_mount(self):
        command = list(runtime.FUNCTIONS_COMMAND)
        self.assertEqual(command[command.index('--event-worker') + 1], '/home/deno/event')
        self.assertEqual(command[command.index('--main-service') + 1], '/home/deno/main')
        self.assertEqual(command[:1], ['start'])

    def test_the_event_worker_is_one_of_the_read_only_mounts(self):
        binds = dict((target, source) for source, target in runtime.functions_binds(E))
        self.assertEqual(Path(binds['/home/deno/event']), runtime.lab.ROOT / 'lab' / 'functions' / 'event-worker')
        self.assertTrue((Path(binds['/home/deno/event']) / 'index.ts').is_file())
        self.assertEqual(sorted(binds), ['/home/deno/event', '/home/deno/functions', '/home/deno/main', '/run/sbarbase'])
        # Nothing of any other environment, and the event worker reads no secrets folder of its own.
        self.assertNotIn('function-digests', str(binds))

    def test_the_readable_check_covers_the_event_worker(self):
        self.assertIn('/home/deno/event/index.ts', runtime.FUNCTIONS_READABLE)
        self.assertIn('/home/deno/event ', runtime.FUNCTIONS_READABLE)
