"""Tier table, launch flags and derived placement arithmetic.

Every expected value in this file is copied from docs/engineering/RESOURCE-POLICY.md
(sections 3.1, 3.2 and 3.5). The tests name the failure each one can produce.
"""
from pathlib import Path
from unittest.mock import patch
import json
import unittest
import resource_policy as policy

# docs/engineering/RESOURCE-POLICY.md section 3.1: (class label, shares, weight, cpus,
# memory, pids), one tuple per row of the design's table.
DESIGN_ROWS = {
    'system.db': ('system', 2048, 800, 1, '1024m', 128),
    'system.storage': ('system', 2048, 800, .5, '512m', 128),
    'system.management-auth': ('system', 1024, 500, .25, '256m', 128),
    'production': ('production', 512, 400, .25, '256m', 128),
    'production.realtime': ('production', 512, 400, .25, '320m', 128),
    'production.functions': ('production', 512, 400, .5, '384m', 256),
    'experimental': ('experimental', 128, 100, .25, '256m', 128),
    'operator.studio': ('operator', 1024, 500, .5, '512m', 128),
    'operator.meta': ('operator', 1024, 500, .25, '256m', 128),
    'maintenance': ('maintenance', 512, 400, None, None, 128),
}
DESIGN_CLASSES = ('system', 'production', 'experimental', 'operator', 'maintenance')


class TierTableTests(unittest.TestCase):
    def test_every_row_matches_the_design_table(self):
        self.assertEqual(set(policy.TIERS), set(DESIGN_ROWS))
        for name, row in DESIGN_ROWS.items():
            entry = policy.TIERS[name]
            self.assertEqual((entry.label, entry.shares, entry.weight, entry.cpus, entry.memory, entry.pids), row, name)

    def test_class_names_are_the_five_in_the_design(self):
        self.assertEqual(policy.CLASSES, DESIGN_CLASSES)
        self.assertEqual({entry.label for entry in policy.TIERS.values()}, set(DESIGN_CLASSES))

    def test_lookup_returns_the_container_flags_for_a_tier(self):
        self.assertEqual(policy.container_flags('production'),
                         {'tier': 'production', 'label': 'production', 'shares': 512, 'weight': 400,
                          'cpus': .25, 'memory': '256m', 'pids': 128})

    def test_unknown_tier_refuses(self):
        self.assertEqual(policy.refusal('nonsense'), 'unknown_tier')
        with self.assertRaises(policy.ResourcePolicyError) as error:
            policy.container_flags('nonsense')
        self.assertEqual(error.exception.reason, 'unknown_tier')

    def test_tier_with_a_missing_weight_refuses(self):
        broken = policy.Tier('production', 512, None, .25, '256m', 128)
        with patch.dict(policy.TIERS, {'production': broken}):
            self.assertEqual(policy.refusal('production'), 'missing_weight')
            with self.assertRaises(policy.ResourcePolicyError) as error:
                policy.container_flags('production')
            self.assertEqual(error.exception.reason, 'missing_weight')

    def test_known_label_accepts_only_the_table_classes(self):
        self.assertTrue(policy.known_label('maintenance'))
        self.assertFalse(policy.known_label('tier-one'))
        self.assertFalse(policy.known_label(None))


def fixed_device(test):
    """Resolve the block device to a fixed name, so a launch needs no real disk.

    The real one comes from findmnt and sysfs, which a container root on overlayfs
    does not have; BlockIOLimits covers that resolution on its own.
    """
    patcher = patch('resource_policy.device', return_value='/dev/sda')
    patcher.start()
    test.addCleanup(patcher.stop)


class LaunchFlagTests(unittest.TestCase):
    """docs/engineering/RESOURCE-POLICY.md section 3.2: the exact flags each launch adds."""

    def setUp(self):
        fixed_device(self)

    def launch(self, tier, memory='256m', cpus=.25, name='sbarbase-durable-probe'):
        import tempfile
        from types import SimpleNamespace
        import durable_runtime as runtime
        target = runtime.Runtime.__new__(runtime.Runtime)
        target.pins = {'auth': {'id': 'fixture'}}
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        with patch.object(runtime, 'inspect', return_value=None), \
             patch.object(runtime, 'PRIVATE', Path(directory.name)), \
             patch.object(runtime.lab, 'docker') as docker:
            docker.return_value = SimpleNamespace(stdout='fixture-id\n')
            target.launch(name, 'auth', {}, memory, cpus, tier=tier)
        return runtime, docker

    def test_launch_passes_the_tier_weight_and_io_flags(self):
        runtime, docker = self.launch('production')
        args = docker.call_args.args
        self.assertEqual(args[args.index('--cpu-shares') + 1], '512')
        self.assertEqual(args[args.index('--blkio-weight') + 1], '400')
        self.assertIn('io.sbarbase.tier=production', args)

    def test_launch_keeps_memory_swap_and_every_existing_flag(self):
        runtime, docker = self.launch('production')
        args = docker.call_args.args
        self.assertEqual(args[args.index('--memory') + 1], args[args.index('--memory-swap') + 1])
        self.assertEqual(args[args.index('--memory') + 1], '256m')
        self.assertEqual(args[args.index('--cpus') + 1], '0.25')
        self.assertEqual(args[args.index('--pids-limit') + 1], '128')
        self.assertIn('io.sbarbase.owner=' + runtime.OWNER, args)
        self.assertIn('--network', args)
        self.assertEqual(args[args.index('--log-opt') + 1], 'max-size=5m')
        self.assertIn('--env-file', args)

    def test_system_rows_use_their_own_weight(self):
        _, docker = self.launch('system.db', '1024m', 1)
        args = docker.call_args.args
        self.assertEqual(args[args.index('--cpu-shares') + 1], '2048')
        self.assertEqual(args[args.index('--blkio-weight') + 1], '800')
        self.assertIn('io.sbarbase.tier=system', args)
        _, docker = self.launch('system.management-auth')
        args = docker.call_args.args
        self.assertEqual(args[args.index('--cpu-shares') + 1], '1024')
        self.assertEqual(args[args.index('--blkio-weight') + 1], '500')

    def test_launch_refuses_a_tier_the_policy_does_not_know(self):
        import durable_runtime as runtime
        with self.assertRaises(policy.ResourcePolicyError):
            self.launch(None)
        with self.assertRaises(policy.ResourcePolicyError):
            self.launch('tier-one')

    def test_launch_refuses_limits_that_disagree_with_the_tier(self):
        with self.assertRaisesRegex(RuntimeError, 'disagrees'):
            self.launch('system.db', '512m', 1)
        with self.assertRaisesRegex(RuntimeError, 'disagrees'):
            self.launch('production', '256m', .5)

    def test_every_launch_call_site_passes_a_tier(self):
        import ast
        import durable_runtime as runtime
        tree = ast.parse(Path(runtime.__file__).read_text())
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and getattr(node.func, 'attr', None) == 'launch']
        # A floor, not an exact count: a new launch site is fine, a launch site
        # without a tier is not. The count was 4 before the mail reconcile added
        # the recreate path in lab/durable_runtime.py.
        self.assertGreaterEqual(len(calls), 4)
        for call in calls:
            self.assertTrue(any(keyword.arg == 'tier' for keyword in call.keywords), ast.unparse(call))


class ProcStub:
    def __init__(self, text):
        self.text = text

    def read_text(self):
        return self.text


PROC = {
    '/proc/meminfo': 'MemAvailable: 9437184 kB\n',
    '/proc/pressure/cpu': 'some avg10=0.0 avg60=0.0 avg300=0.0 total=0\n',
    '/proc/pressure/io': 'full avg10=0.0 avg60=0.0 avg300=0.0 total=0\n',
    '/proc/pressure/memory': 'full avg10=0.0 avg60=0.0 avg300=0.0 total=0\n',
}


class DerivedPlacementTests(unittest.TestCase):
    """docs/engineering/RESOURCE-POLICY.md section 3.5: the counted set is derived, not listed."""

    def setUp(self):
        import socket
        from types import SimpleNamespace
        import combined_admission
        import durable_runtime as runtime
        self.socket, self.SimpleNamespace = socket, SimpleNamespace
        self.admission, self.runtime = combined_admission, runtime
        self.MIB = combined_admission.MIB
        self.prefix = 'sbarbase-restore-aaaaaaaaaaaa'
        self.environments = ['e_' + digit * 24 for digit in '0123']
        self.source = SimpleNamespace(values={'environments': {name: {} for name in self.environments}})
        self.target = SimpleNamespace(prefix=self.prefix)
        self.items, self.running, self.calls = {}, set(), []

    def add(self, name, owner, tier, memory_mib, cpus, shares, weight, pids=128, running=True):
        self.items[name] = {
            'Id': 'sha256:' + name,
            'Config': {'Labels': {'io.sbarbase.owner': owner, 'io.sbarbase.tier': tier}},
            'State': {'Running': running},
            'HostConfig': {'Memory': memory_mib * self.MIB, 'MemorySwap': memory_mib * self.MIB,
                           'NanoCpus': int(cpus * 1_000_000_000), 'CpuShares': shares,
                           'BlkioWeight': weight, 'PidsLimit': pids}}
        if running:
            self.running.add(name)
        else:
            self.running.discard(name)

    def placement(self):
        add = self.add
        add(self.runtime.DB, 'durable-upstream', 'system', 1024, 1, 2048, 800)
        add(self.runtime.PREFIX + '-storage', 'durable-upstream', 'system', 512, .5, 2048, 800)
        add(self.runtime.PREFIX + '-management-auth', 'durable-upstream', 'system', 256, .25, 1024, 500)
        for environment in self.environments:
            add(self.runtime.PREFIX + '-' + environment + '-auth', 'durable-upstream', 'production', 256, .25, 512, 400)
            add(self.runtime.PREFIX + '-' + environment + '-rest', 'durable-upstream', 'production', 256, .25, 512, 400)
        add(self.prefix + '-db', 'recovery-target', 'maintenance', 1024, 1, 512, 400)
        add(self.prefix + '-auth', 'recovery-target', 'maintenance', 256, .25, 512, 400)
        add(self.prefix + '-rest', 'recovery-target', 'maintenance', 256, .25, 512, 400)
        add(self.prefix + '-storage', 'recovery-target', 'maintenance', 512, .5, 512, 400)

    def docker(self, *args, **kwargs):
        self.calls.append(args)
        if args[:1] == ('ps',):
            # The key only filter (label=io.sbarbase.owner) sees every labelled container;
            # the owner=value form is what a caller that asks for one owner still gets.
            owner = next((argument.rsplit('=', 1)[1] for argument in args
                          if argument.startswith('label=io.sbarbase.owner=')), '')
            names = [name for name in self.items
                     if not owner or self.items[name]['Config']['Labels']['io.sbarbase.owner'] == owner]
            if '--no-trunc' in args:
                identity = [self.items[name]['Id'] for name in names if name in self.running]
                return self.SimpleNamespace(stdout='\n'.join(identity), returncode=0)
            return self.SimpleNamespace(stdout='\n'.join(names) + ('\n' if names else ''), returncode=0)
        if args[:1] == ('info',):
            return self.SimpleNamespace(stdout=json.dumps({'Name': self.socket.gethostname(), 'OSType': 'linux'}), returncode=0)
        if args[:1] == ('inspect',):
            key = args[1]
            item = self.items.get(key) or next((value for value in self.items.values() if value['Id'] == key), None)
            if item is None:
                raise AssertionError('unknown inspect ' + key)
            return self.SimpleNamespace(stdout=json.dumps([item]), returncode=0)
        raise AssertionError('unexpected docker ' + repr(args))

    def check(self):
        with patch.object(self.admission.lab, 'docker', self.docker), \
             patch.object(self.admission, 'Path', lambda path: ProcStub(PROC[path])):
            admission = self.admission.CombinedAdmission(self.source, self.target)
            return admission.check_current()

    def counted(self):
        with patch.object(self.admission.lab, 'docker', self.docker):
            return self.admission.counted_names(self.target)

    def test_a_container_the_literal_list_never_named_is_counted(self):
        self.placement()
        studio = self.runtime.PREFIX + '-' + self.environments[0] + '-studio'
        self.add(studio, 'durable-upstream', 'operator', 512, .5, 1024, 500)
        counted = self.counted()
        self.assertIn(studio, counted)
        self.assertEqual(set(counted), set(self.items))

    def test_owned_container_outside_the_recorded_placement_refuses(self):
        self.placement()
        self.add('sbarbase-lab-db', 'durable-upstream', 'system', 1024, 1, 2048, 800)
        with self.assertRaisesRegex(RuntimeError, 'recorded placement'):
            self.counted()

    def test_a_second_recovery_target_generation_is_reported_not_refused(self):
        """Two retained generations share one owner label; only the recorded one is counted.

        Eight stopped containers of two recovery-target generations exist on this
        host, so requiring every container of that owner to match the recorded
        target's prefix made the installation start path refuse for a reason that
        is not a resource, while HEAD^ computed a plan and admitted.
        """
        self.placement()
        other = 'sbarbase-restore-bbbbbbbbbbbb'
        for kind, memory, cpus in (('db', 1024, 1), ('auth', 256, .25), ('rest', 256, .25), ('storage', 512, .5)):
            self.add(other + '-' + kind, 'recovery-target', 'maintenance', memory, cpus, 512, 400, running=False)
        metrics = self.check()
        self.assertEqual(metrics['planned_memory_mib'], 5888)
        self.assertEqual(metrics['planned_cpus'], 5.75)
        self.assertEqual(metrics['unrecorded_stopped'], sorted(other + '-' + kind for kind in ('db', 'auth', 'rest', 'storage')))

    def test_a_running_container_under_an_unrecorded_owner_refuses(self):
        """A third owner label escapes the two filtered queries and spends the ceiling.

        lab/mail-check.py launches its probe as io.sbarbase.owner=mail-probe while the
        retained runtime is up, so a running container this module was never told
        about has to be a refusal, not an omission.
        """
        self.placement()
        self.add('sbarbase-notify-mailpit-aaaaaaaa', 'notification-check', 'operator', 128, .25, 1024, 500)
        with self.assertRaisesRegex(RuntimeError, 'running'):
            self.counted()
        with self.assertRaisesRegex(RuntimeError, 'running'):
            self.check()

    def test_a_stopped_container_under_an_unrecorded_owner_is_named_not_silent(self):
        """A stopped container consumes nothing, so it is reported instead of refused."""
        self.placement()
        self.add('sbarbase-lab-db', 'component-lab', 'system', 1024, 1, 2048, 800, running=False)
        counted = self.counted()
        self.assertNotIn('sbarbase-lab-db', counted)
        self.assertIn('sbarbase-lab-db', self.check()['unrecorded_stopped'])

    def test_ceiling_check_sees_the_container_the_list_omitted(self):
        self.placement()
        metrics = self.check()
        self.assertEqual(metrics['planned_memory_mib'], 5888)
        self.assertEqual(metrics['planned_cpus'], 5.75)
        self.add(self.runtime.PREFIX + '-' + self.environments[0] + '-studio', 'durable-upstream', 'operator', 512, .5, 1024, 500)
        with self.assertRaisesRegex(RuntimeError, 'installation_ceiling'):
            self.check()

    def test_recorded_placement_container_must_exist(self):
        self.placement()
        del self.items[self.runtime.PREFIX + '-' + self.environments[0] + '-rest']
        with self.assertRaisesRegex(RuntimeError, 'absent'):
            self.check()

    def test_counted_container_must_carry_a_policy_tier(self):
        self.placement()
        name = self.runtime.PREFIX + '-' + self.environments[0] + '-auth'
        self.items[name]['Config']['Labels'].pop('io.sbarbase.tier')
        with self.assertRaisesRegex(RuntimeError, 'no policy tier'):
            self.check()
        self.items[name]['Config']['Labels']['io.sbarbase.tier'] = 'tier-one'
        with self.assertRaisesRegex(RuntimeError, 'no policy tier'):
            self.check()

    def test_counted_container_must_carry_a_finite_limit(self):
        import combined_admission
        fixture = {'Id': 'sha256:fixture', 'Config': {'Labels': {'io.sbarbase.owner': 'durable-upstream', 'io.sbarbase.tier': 'production'}},
                   'HostConfig': {'Memory': self.MIB, 'MemorySwap': self.MIB, 'NanoCpus': 250_000_000, 'CpuShares': 512, 'BlkioWeight': 400, 'PidsLimit': 128}}
        for field in combined_admission.FINITE_LIMITS:
            item = dict(fixture, HostConfig=dict(fixture['HostConfig'], **{field: 0}))
            with self.assertRaisesRegex(RuntimeError, 'unbounded ' + field):
                self.admission.limits(item)
        swapped = dict(fixture, HostConfig=dict(fixture['HostConfig'], MemorySwap=2 * self.MIB))
        with self.assertRaisesRegex(RuntimeError, 'swap is not disabled'):
            self.admission.limits(swapped)


class MaintenanceLabels(unittest.TestCase):
    """The flags a target script adds to a container it launches itself.

    A counted container without the class label is a refusal in
    lab/combined_admission.py, so every owner labelled creation site carries it.
    """

    def setUp(self):
        fixed_device(self)

    def test_labels_carry_the_class_the_weights_and_the_io_flags(self):
        flags = policy.labels('maintenance')
        self.assertEqual(flags[:6], ['--label', 'io.sbarbase.tier=maintenance',
                                     '--cpu-shares', '512', '--blkio-weight', '400'])
        self.assertEqual(flags[6:], policy.io_flags('maintenance'))

    def test_labels_refuse_an_unknown_tier(self):
        with self.assertRaisesRegex(policy.ResourcePolicyError, 'unknown_tier'):
            policy.labels('nope')

    def test_every_recovery_target_creation_site_carries_the_label(self):
        # The rule is only real if every site obeys it, so read the sites.
        for name in ('recovery-restore-db.py', 'recovery-check-services.py',
                     'recovery-check-storage.py', 'target-hba-check.py'):
            text = (Path(__file__).parent / name).read_text()
            sites = [line for line in text.splitlines()
                     if "io.sbarbase.owner='+OWNER," in line and "docker('run'" in line]
            self.assertTrue(sites, name + ' has no owner labelled run site to check')
            for line in sites:
                self.assertIn("resource_policy.labels(", line, name)


class BlockIOLimits(unittest.TestCase):
    """The block IO mechanism that binds, since the weight flag was measured not to.

    docs/engineering/RESOURCE-POLICY.md section 3.1.1 and
    docs/evidence/resource-policy-cgroup-mapping.json carry the measurement.
    """

    @staticmethod
    def _bytes(text):
        units = {'kb': 1024, 'mb': 1024 ** 2, 'gb': 1024 ** 3}
        return int(text[:-2]) * units[text[-2:]]

    def test_every_tier_in_the_table_has_an_io_row(self):
        # Two tables for one policy drift apart unless something checks them.
        self.assertEqual(sorted(policy.IO_LIMITS), sorted(policy.TIERS))

    def test_the_io_order_follows_the_tier_order(self):
        self.assertGreater(self._bytes(policy.IO_LIMITS['system.db'][0]),
                           self._bytes(policy.IO_LIMITS['production'][0]))
        self.assertGreater(self._bytes(policy.IO_LIMITS['production'][0]),
                           self._bytes(policy.IO_LIMITS['experimental'][0]))
        self.assertGreater(policy.IO_LIMITS['production'][2], policy.IO_LIMITS['experimental'][2])

    def test_io_flags_put_the_resolved_device_on_every_limit(self):
        flags = policy.io_flags('production', '/dev/fixture0')
        self.assertEqual(len(flags), 8)
        self.assertEqual(flags[0], '--device-read-bps')
        self.assertEqual(flags[1], '/dev/fixture0:64mb')
        self.assertEqual(flags[6], '--device-write-iops')
        self.assertEqual(flags[7], '/dev/fixture0:1000')

    def test_io_flags_refuse_an_unknown_tier(self):
        with self.assertRaisesRegex(policy.ResourcePolicyError, 'unknown_tier'):
            policy.io_flags('nope', '/dev/fixture0')

    def test_io_flags_refuse_when_no_device_could_be_resolved(self):
        # A container launched without IO separation is the failure this refuses.
        with patch('resource_policy.device', return_value=None):
            with self.assertRaisesRegex(policy.ResourcePolicyError, 'io_device_unavailable'):
                policy.io_flags('production')

    def test_io_device_takes_the_device_from_a_subvolume_mount_source(self):
        # This host's own device is the fixture, with a subvolume suffix added,
        # because io_device refuses a device path that does not exist.
        source = policy._findmnt('/')
        if not source or not source.startswith('/dev/'):
            self.skipTest('this host reports no device source for /')
        device = source.split('[')[0]
        # A partition resolves to its whole disk, which is what io.max accepts.
        self.assertEqual(policy.io_device('/', runner=lambda target: device + '[/root]'), policy.whole_disk(device))

    def test_a_partition_is_limited_through_its_whole_disk(self):
        # Mirrors sysfs: /sys/class/block/vda3 links into the disk's own directory.
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'devices' / 'vda' / 'vda3').mkdir(parents=True)
            (root / 'devices' / 'vda' / 'vda3' / 'partition').write_text('3')
            (root / 'devices' / 'dm-0').mkdir(parents=True)
            sysfs = root / 'class'
            sysfs.mkdir()
            (sysfs / 'vda3').symlink_to(root / 'devices' / 'vda' / 'vda3')
            (sysfs / 'dm-0').symlink_to(root / 'devices' / 'dm-0')
            self.assertEqual(policy.whole_disk('/dev/vda3', sysfs=sysfs), '/dev/vda')
            self.assertEqual(policy.whole_disk('/dev/dm-0', sysfs=sysfs), '/dev/dm-0')
            self.assertEqual(policy.whole_disk('/dev/sdz9', sysfs=sysfs), '/dev/sdz9')

    def test_a_device_known_to_sysfs_but_absent_from_dev_is_accepted(self):
        # A control plane in a container sees the host's sysfs but not its /dev.
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            sysfs = Path(directory)
            (sysfs / 'vdz').mkdir()
            self.assertTrue(policy.known_block_device('/dev/vdz', sysfs=sysfs))
            self.assertFalse(policy.known_block_device('/dev/vdy', sysfs=sysfs))

    def test_a_kernel_root_name_resolves_through_the_device_numbers(self):
        # Inside a container findmnt can report /dev/root, which has no node or sysfs name.
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'devices' / 'sda' / 'sda1').mkdir(parents=True)
            (root / 'dev-block').mkdir()
            (root / 'dev-block' / '8:1').symlink_to(root / 'devices' / 'sda' / 'sda1')
            self.assertEqual(policy.device_by_numbers('8:1', sysfs=root / 'dev-block'), '/dev/sda1')
            self.assertIsNone(policy.device_by_numbers('8:2', sysfs=root / 'dev-block'))
            self.assertIsNone(policy.device_by_numbers('0:45', sysfs=root / 'dev-block'))
            self.assertIsNone(policy.device_by_numbers('../x', sysfs=root / 'dev-block'))

    def test_io_device_falls_back_to_numbers_when_the_name_is_unknown(self):
        known = {'/dev/sda1', '/dev/sda'}
        with patch.object(policy, '_findmnt', return_value='/dev/root'), \
             patch.object(policy, 'numbers', return_value='8:1'), \
             patch.object(policy, 'device_by_numbers', side_effect=lambda value: '/dev/sda1' if value == '8:1' else None), \
             patch.object(policy, 'known_block_device', side_effect=lambda device: device in known), \
             patch.object(policy, 'whole_disk', side_effect=lambda device: '/dev/sda'):
            self.assertEqual(policy.io_device('/'), '/dev/sda')
        with patch.object(policy, '_findmnt', return_value='/dev/root'), \
             patch.object(policy, 'numbers', return_value='0:40'), \
             patch.object(policy, 'known_block_device', return_value=False):
            self.assertIsNone(policy.io_device('/'))

    def test_io_device_refuses_a_source_that_is_not_a_block_device(self):
        for source in ('', 'overlay', 'tmpfs', 'none', '/dev/does-not-exist'):
            self.assertIsNone(policy.io_device('/tmp', runner=lambda target, s=source: s), source)

    def test_the_launch_path_passes_the_io_flags(self):
        text = (Path(__file__).parent / 'durable_runtime.py').read_text()
        self.assertIn('*resource_policy.io_flags(tier)', text)


if __name__ == '__main__':
    unittest.main()