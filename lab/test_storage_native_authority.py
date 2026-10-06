"""Private source parsing/protocol tests, never original-native acceptance."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import storage_native_authority as authority
from test_storage_write_settlement import manifest


def candidate():
    declared = manifest()
    declared['binding']['coverage'] = 'dedicated-resources'
    declared['neighbors'] = []
    declared['features'] = {name: name in ('standard', 'signed') for name in declared['features']}
    cid = declared['writers'][0]['container']
    config = {'Env': ['STORAGE_BACKEND=file', 'MULTI_TENANT=false'],
              'Labels': {'sbarbase.owner': declared['writers'][0]['owner'],
                         'sbarbase.runtime': declared['binding']['runtime'],
                         'sbarbase.operation': declared['operation']}}
    raw = {'Id': cid, 'Image': 'sha256:' + '8' * 64, 'Config': config,
           'HostConfig': {'RestartPolicy': {'Name': 'no', 'MaximumRetryCount': 0},
                          'Privileged': False, 'PidMode': '', 'VolumesFrom': None,
                          'NetworkMode': 'test-private', 'CapAdd': None},
           'Mounts': [{'Type': 'volume', 'Name': declared['volume']['id'],
                       'Source': declared['volume']['root'], 'Destination': '/var/lib/storage', 'RW': True}],
           'State': {'Running': True, 'StartedAt': declared['writers'][0]['started_at'],
                     'Paused': False, 'Restarting': False, 'Dead': False, 'OOMKilled': False,
                     'Pid': 100, 'Status': 'running', 'ExitCode': 0}}
    declared['writers'][0]['config_sha256'] = authority.config_digest(raw)
    process = {'pid': 100, 'start_ticks': 500, 'mount_namespace': 123, 'pid_namespace': 124}
    db_raw = {'Id': declared['database']['container'], 'Image': 'sha256:' + 'a' * 64,
              'Config': {'Env': [], 'Labels': {}}, 'HostConfig': {}, 'Mounts': [],
              'State': {'Running': True, 'StartedAt': '2026-10-06T01:00:00.000Z',
                        'Paused': False, 'Restarting': False, 'Dead': False, 'OOMKilled': False,
                        'Pid': 101, 'Status': 'running', 'ExitCode': 0}}
    db_process = {'pid': 101, 'start_ticks': 400, 'mount_namespace': 123, 'pid_namespace': 124}
    db_fingerprint = authority.digest(db_raw)
    declared['neighbors'] = [db_fingerprint]
    spec = {'version': 1, 'manifest': declared, 'image_config_id': raw['Image'],
            'internal_bucket': 'stub', 'version_separator': '-$v-', 'processes': {cid: [process]},
            'protected_containers': {db_raw['Id']: db_fingerprint},
            'database_incarnation': {'container': db_raw['Id'], 'image_config_id': db_raw['Image'],
                                     'started_at': db_raw['State']['StartedAt'], 'init_process': db_process,
                                     'config_sha256': authority.config_digest(db_raw)}}
    material = {key: copy.deepcopy(declared[key]) for key in
                ('daemon', 'namespace', 'operation', 'generation', 'management_epoch', 'volume')}
    material.update(containers_before=[copy.deepcopy(raw), copy.deepcopy(db_raw)],
                    containers_after=[copy.deepcopy(raw), copy.deepcopy(db_raw)],
                    image={'Id': raw['Image'], 'RepoDigests': ['public.ecr.aws/supabase/storage-api@' + authority.IMAGE]},
                    processes={cid: [copy.deepcopy(process)]}, database_process=copy.deepcopy(db_process),
                    database_image={'Id': db_raw['Image'],
                                    'RepoDigests': ['original-db@' + declared['database']['image']]})
    return spec, material


class NativeObservationParsingTests(unittest.TestCase):
    def setUp(self):
        self.spec, self.material = candidate()
        self.cid = self.spec['manifest']['writers'][0]['container']

    def refuse(self, change, spec_change=None):
        change(self.material)
        if spec_change:
            spec_change(self.spec)
        with self.assertRaises(authority.Refused):
            authority.decode_inventory(self.spec, self.material)

    def raw_change(self, change):
        for key in ('containers_before', 'containers_after'):
            change(self.material[key][0])

    def test_valid_parse_explicitly_denies_native_admission(self):
        value = authority.decode_inventory(self.spec, self.material)
        self.assertFalse(value['native_admitted'])
        self.assertIn('compiled-image-provenance', value['pending'])
        self.material['processes'][self.cid][0]['pid'] = 200
        self.assertEqual(value['writers'][0]['processes'][0]['pid'], 100)

    def test_image_config_identity_does_not_equal_registry_manifest_digest(self):
        self.assertNotEqual(self.spec['image_config_id'], authority.IMAGE)
        self.assertFalse(authority.decode_inventory(self.spec, self.material)['native_admitted'])

    def test_registry_pin_without_config_association_refuses(self):
        self.refuse(lambda value: value['image'].update(Id=authority.IMAGE))

    def test_config_id_without_registry_association_refuses(self):
        self.refuse(lambda value: value['image'].update(RepoDigests=[]))

    def test_registry_suffix_with_wrong_digest_refuses(self):
        self.refuse(lambda value: value['image'].update(RepoDigests=['repo@sha256:' + 'f' * 64]))

    def test_unknown_envelope_field_refuses(self):
        self.refuse(lambda value: value.update(ingress='closed'))

    def test_type_substitution_for_generation_refuses(self):
        self.refuse(lambda value: value.update(generation=4.0))

    def test_type_substitution_for_management_epoch_refuses(self):
        self.refuse(lambda value: value.update(management_epoch=8.0))

    def test_operation_changed_refuses(self):
        self.refuse(lambda value: value.update(operation='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'))

    def test_daemon_changed_refuses(self):
        self.refuse(lambda value: value.update(daemon='0' * 64))

    def test_namespace_changed_refuses(self):
        self.refuse(lambda value: value.update(namespace='other-host'))

    def test_volume_mount_changed_refuses(self):
        self.refuse(lambda value: value['volume'].update(mount_id=57))

    def test_launch_race_between_inspections_refuses(self):
        self.refuse(lambda value: value['containers_after'][0]['State'].update(StartedAt='2026-10-06T04:00:00Z'))

    def test_unlisted_container_refuses(self):
        def change(value):
            for key in ('containers_before', 'containers_after'):
                extra = copy.deepcopy(value[key][0])
                extra['Id'] = '9' * 64
                value[key].append(extra)
        self.refuse(change)

    def test_neighbor_exact_observation_is_protected(self):
        neighbor = {'Id': '9' * 64, 'State': {'Running': True}, 'private': 'not-written-to-evidence'}
        fingerprint = authority.digest(neighbor)
        self.spec['protected_containers'][neighbor['Id']] = fingerprint
        self.spec['manifest']['neighbors'].append(fingerprint)
        for key in ('containers_before', 'containers_after'):
            self.material[key].append(copy.deepcopy(neighbor))
        self.assertFalse(authority.decode_inventory(self.spec, self.material)['native_admitted'])
        for key in ('containers_before', 'containers_after'):
            self.material[key][2]['State']['Running'] = False
        with self.assertRaises(authority.Refused):
            authority.decode_inventory(self.spec, self.material)

    def test_restart_policy_change_refuses(self):
        self.refuse(lambda _: self.raw_change(lambda raw: raw['HostConfig']['RestartPolicy'].update(Name='always')))

    def test_exactly_enrolled_but_global_pid_reach_refuses(self):
        self.raw_change(lambda raw: raw['HostConfig'].update(PidMode='host'))
        self.spec['manifest']['writers'][0]['config_sha256'] = authority.config_digest(self.material['containers_before'][0])
        with self.assertRaises(authority.Refused):
            authority.decode_inventory(self.spec, self.material)

    def test_exactly_enrolled_but_multitenant_refuses(self):
        self.raw_change(lambda raw: raw['Config']['Env'].append('IS_MULTITENANT=true'))
        self.spec['manifest']['writers'][0]['config_sha256'] = authority.config_digest(self.material['containers_before'][0])
        with self.assertRaises(authority.Refused):
            authority.decode_inventory(self.spec, self.material)

    def test_duplicate_effective_env_refuses(self):
        self.raw_change(lambda raw: raw['Config']['Env'].append('STORAGE_BACKEND=file'))
        self.spec['manifest']['writers'][0]['config_sha256'] = authority.config_digest(self.material['containers_before'][0])
        with self.assertRaises(authority.Refused):
            authority.decode_inventory(self.spec, self.material)

    def test_pid_start_identity_changed_refuses(self):
        self.refuse(lambda value: value['processes'][self.cid][0].update(start_ticks=501))

    def test_pid_namespace_changed_refuses(self):
        self.refuse(lambda value: value['processes'][self.cid][0].update(pid_namespace=200))

    def test_process_identity_decodes_parenthesized_names(self):
        fields = ['S'] + ['0'] * 18 + ['500'] + ['0'] * 5
        value = authority.process_identity(100, ('100 (name with ) space) ' + ' '.join(fields)).encode(), 123, 124)
        self.assertEqual(value, self.spec['processes'][self.cid][0])

    def test_malformed_process_stat_refuses(self):
        with self.assertRaises(authority.Refused):
            authority.process_identity(100, b'wrong', 123, 124)

    def test_clean_exit_parse_remains_unaccepted(self):
        self.raw_change(lambda raw: raw['State'].update(Running=False, Pid=0, Status='exited'))
        self.material['processes'][self.cid] = []
        self.assertFalse(authority.decode_inventory(self.spec, self.material, stopped=True)['native_admitted'])

    def test_exited_container_with_live_pid_refuses(self):
        self.raw_change(lambda raw: raw['State'].update(Running=False, Pid=0, Status='exited'))
        with self.assertRaises(authority.Refused):
            authority.decode_inventory(self.spec, self.material, stopped=True)

    def test_forced_exit_requires_reconciliation(self):
        self.raw_change(lambda raw: raw['State'].update(Running=False, Pid=0, Status='exited', ExitCode=137))
        self.material['processes'][self.cid] = []
        with self.assertRaises(authority.Refused):
            authority.decode_inventory(self.spec, self.material, stopped=True)

    def test_shared_or_fixture_enrollment_refuses(self):
        for coverage in ('complete-shared-resources', 'disposable-fixture', None):
            with self.subTest(coverage=coverage), self.assertRaises(authority.Refused):
                value = copy.deepcopy(self.spec)
                value['manifest']['binding']['coverage'] = coverage
                authority.validate_spec(value)

    def test_duplicate_json_fields_refuse(self):
        with self.assertRaises(authority.Refused):
            authority.json_material(b'{"a":1,"a":2}')

    def test_nonfinite_json_refuses(self):
        with self.assertRaises(authority.Refused):
            authority.json_material(b'{"a":NaN}')

    def test_all_live_commands_refuse_before_subprocess(self):
        with tempfile.TemporaryDirectory() as root, patch('storage_native_authority.subprocess.run') as run:
            local = authority.LocalNativeAuthority(self.spec, root)
            for call in (local.observe, local.stop, local.recover_stop, local.reconcile,
                         local.commands.containers, local.commands.image, local.commands.stop,
                         local.commands.storage_rows):
                with self.subTest(call=call.__name__), self.assertRaises(authority.Refused):
                    call()
            run.assert_not_called()
            self.assertEqual(list(Path(root).iterdir()), [])

    def test_database_must_be_in_protected_inventory(self):
        self.spec['protected_containers'] = {}
        self.spec['manifest']['neighbors'] = []
        with self.assertRaises(authority.Refused):
            authority.validate_spec(self.spec)

    def test_database_registry_pin_requires_its_own_config_association(self):
        self.material['database_image']['RepoDigests'] = self.material['image']['RepoDigests']
        with self.assertRaises(authority.Refused):
            authority.decode_inventory(self.spec, self.material)

    def test_database_config_image_association_drift_refuses(self):
        self.material['database_image']['Id'] = self.spec['image_config_id']
        with self.assertRaises(authority.Refused):
            authority.decode_inventory(self.spec, self.material)

    def test_database_process_start_drift_refuses(self):
        self.material['database_process']['start_ticks'] += 1
        with self.assertRaises(authority.Refused):
            authority.decode_inventory(self.spec, self.material)

    def test_database_namespace_drift_refuses(self):
        self.material['database_process']['mount_namespace'] += 1
        with self.assertRaises(authority.Refused):
            authority.decode_inventory(self.spec, self.material)

    def test_database_pid_type_substitution_refuses(self):
        self.material['database_process']['pid'] = 101.0
        with self.assertRaises(authority.Refused):
            authority.decode_inventory(self.spec, self.material)

    def test_database_enrolled_start_ticks_boolean_refuses(self):
        self.spec['database_incarnation']['init_process']['start_ticks'] = True
        with self.assertRaises(authority.Refused):
            authority.validate_spec(self.spec)

    def test_database_restart_refuses_even_if_neighbor_fingerprint_reenrolled(self):
        for key in ('containers_before', 'containers_after'):
            self.material[key][1]['State']['StartedAt'] = '2026-10-06T03:00:00Z'
        fingerprint = authority.digest(self.material['containers_before'][1])
        db_cid = self.spec['database_incarnation']['container']
        self.spec['protected_containers'][db_cid] = fingerprint
        self.spec['manifest']['neighbors'] = [fingerprint]
        with self.assertRaises(authority.Refused):
            authority.decode_inventory(self.spec, self.material)

    def test_direct_sql_capture_running_writer_refuses_before_command(self):
        commands = authority.DockerLocalCommands(self.spec)
        with patch('storage_native_authority._installed_launch_guard', return_value={}), \
             patch.object(commands, 'material', return_value=self.material), \
             patch.object(commands, '_run') as run:
            with self.assertRaises(authority.Refused):
                commands.storage_rows()
            run.assert_not_called()

    def test_reverse_enrolled_stopped_writers_preserve_manifest_order(self):
        # Pure Docker observation parsing, with no original execution or proof.
        original_writer = copy.deepcopy(self.spec['manifest']['writers'][0])
        original_raw = copy.deepcopy(self.material['containers_before'][0])
        database_raw = copy.deepcopy(self.material['containers_before'][1])
        enrollment = ['b' * 64, 'a' * 64]
        writers, observed, processes = [], [], {}
        for index, cid in enumerate(enrollment):
            writer = copy.deepcopy(original_writer)
            writer.update(container=cid, role='api' if index == 0 else 'worker')
            writers.append(writer)
            raw = copy.deepcopy(original_raw)
            raw['Id'] = cid
            raw['State'].update(Running=False, Pid=0, Status='exited', ExitCode=0)
            observed.append(raw)
            processes[cid] = [{'pid': 100 + index, 'start_ticks': 500 + index,
                               'mount_namespace': 123, 'pid_namespace': 124}]
        self.spec['manifest']['writers'] = writers
        self.spec['processes'] = processes
        inventory = sorted([database_raw, *observed], key=lambda raw: raw['Id'])
        self.material['containers_before'] = copy.deepcopy(inventory)
        self.material['containers_after'] = copy.deepcopy(inventory)
        self.material['processes'] = {cid: [] for cid in enrollment}
        decoded = authority.decode_inventory(self.spec, self.material, stopped=True)
        stopped = authority.stopped_observation(self.spec, decoded)
        self.assertEqual([entry['container'] for entry in stopped['writers']], enrollment)
        self.assertFalse(stopped['native_admitted'])
        self.assertTrue(all(entry['stopped'] and entry['processes'] == [] for entry in stopped['writers']))


class PrivateOwnerLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.spec, _ = candidate()
        self.ledger = authority.OwnerLedger(self.root)

    def test_before_effect_intent_survives_reopen_without_replay(self):
        with self.ledger.locked() as ledger:
            intent = ledger.append(self.spec, 'intent', '1' * 64)
        with self.ledger.locked() as ledger:
            self.assertEqual(ledger.records, [intent])
            with self.assertRaises(authority.Refused):
                ledger.append(self.spec, 'intent', '1' * 64)
            observed = ledger.append(self.spec, 'observed', '2' * 64)
        self.assertEqual(observed['sequence'], 2)

    def test_ack_is_not_observed_exit(self):
        with self.ledger.locked() as ledger:
            ledger.append(self.spec, 'intent', '1' * 64)
            ledger.append(self.spec, 'ack', '1' * 64)
        with self.ledger.locked() as ledger:
            self.assertEqual(ledger.records[-1]['phase'], 'ack')

    def test_interrupted_append_refuses_without_repair(self):
        target = self.root / 'owner.jsonl'
        target.write_bytes(b'{"partial":')
        target.chmod(0o600)
        with self.assertRaises(authority.Refused), self.ledger.locked():
            pass
        self.assertEqual(target.read_bytes(), b'{"partial":')

    def test_hash_corruption_refuses(self):
        with self.ledger.locked() as ledger:
            ledger.append(self.spec, 'intent', '1' * 64)
        target = self.root / 'owner.jsonl'
        data = target.read_bytes().replace(b'"generation":4', b'"generation":5')
        target.write_bytes(data)
        with self.assertRaises(authority.Refused), self.ledger.locked():
            pass

    def test_changed_management_epoch_cannot_ack_prior_intent(self):
        with self.ledger.locked() as ledger:
            ledger.append(self.spec, 'intent', '1' * 64)
        other = copy.deepcopy(self.spec)
        other['manifest']['management_epoch'] += 1
        with self.ledger.locked() as ledger, self.assertRaises(authority.Refused):
            ledger.append(other, 'ack', '1' * 64)

    def test_symlink_ledger_refuses(self):
        target = self.root / 'other'
        target.write_bytes(b'')
        target.chmod(0o600)
        (self.root / 'owner.jsonl').symlink_to(target)
        with self.assertRaises(authority.Refused), self.ledger.locked():
            pass

    def test_hardlink_ledger_refuses(self):
        target = self.root / 'other'
        target.write_bytes(b'')
        target.chmod(0o600)
        os.link(target, self.root / 'owner.jsonl')
        with self.assertRaises(authority.Refused), self.ledger.locked():
            pass

    def test_parallel_owner_refuses(self):
        with self.ledger.locked():
            with self.assertRaises(authority.Refused), self.ledger.locked():
                pass

    def test_unowned_or_public_directory_refuses(self):
        self.root.chmod(0o755)
        with self.assertRaises(authority.Refused), self.ledger.locked():
            pass

    def test_ack_before_intent_refuses(self):
        with self.ledger.locked() as ledger, self.assertRaises(authority.Refused):
            ledger.append(self.spec, 'ack', '1' * 64)


class PrivateOriginalFileParsingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.spec, _ = candidate()
        self.relative = 'stub/' + self.spec['manifest']['binding']['runtime'] + '/bucket/key-$v-version'
        self.file = self.root / self.relative
        self.file.parent.mkdir(parents=True)
        self.file.write_bytes(b'original-bytes')
        os.utime(self.file, ns=(1700000000000000123, 1700000000000000456))
        fd = os.open(self.root, os.O_DIRECTORY)
        try:
            info = os.fstat(fd)
            self.identity = {'device': info.st_dev, 'inode': info.st_ino, 'mount_id': authority._mount_id(fd)}
        finally:
            os.close(fd)
        self.database = {'database_oid': self.spec['manifest']['database']['oid'], 'sessions': [], 'prepared': [],
                         'objects': [{'id': 'row', 'bucket_id': 'bucket', 'name': 'key', 'version': 'version',
                                      'metadata': {'size': len(b'original-bytes'), 'eTag': 'native-opaque'}}]}

    def observe(self):
        return authority.snapshot_files(str(self.root), self.identity)

    def test_exact_native_nsmtime_bytes_and_metadata_are_read_without_rewrite(self):
        before = self.file.stat()
        files = self.observe()
        self.assertEqual(files[0]['mtime_ns'], 1700000000000000456)
        self.assertEqual(files[0]['sha256'], hashlib.sha256(b'original-bytes').hexdigest())
        self.assertEqual(self.file.stat().st_mtime_ns, before.st_mtime_ns)
        result = authority.reconcile_material(self.spec, self.database, files)
        self.assertEqual(result['unresolved'], [])
        self.assertFalse(result['native_admitted'])
        self.assertEqual(self.database['objects'][0]['metadata']['eTag'], 'native-opaque')

    def test_same_version_same_length_change_has_different_byte_digest(self):
        original = self.observe()[0]['sha256']
        self.file.write_bytes(b'changed--bytes')
        os.utime(self.file, ns=(1700000000000000123, 1700000000000000456))
        self.assertNotEqual(original, self.observe()[0]['sha256'])

    def test_xattr_observation_preserves_original_bytes(self):
        os.setxattr(self.file, 'user.source-test', b'native-metadata')
        files = self.observe()
        self.assertEqual(files[0]['xattrs']['user.source-test'], b'native-metadata'.hex())
        self.assertEqual(os.getxattr(self.file, 'user.source-test'), b'native-metadata')

    def test_symlink_file_refuses(self):
        (self.file.parent / 'link').symlink_to(self.file)
        with self.assertRaises(authority.Refused):
            self.observe()

    def test_hardlink_file_refuses(self):
        os.link(self.file, self.file.parent / 'other')
        with self.assertRaises(authority.Refused):
            self.observe()

    def test_root_descriptor_identity_changed_refuses(self):
        self.identity['inode'] += 1
        with self.assertRaises(authority.Refused):
            self.observe()

    def test_byte_budget_refuses(self):
        with self.assertRaises(authority.Refused):
            authority.snapshot_files(str(self.root), self.identity, max_bytes=2)

    def test_missing_reachable_version_is_unresolved(self):
        value = authority.reconcile_material(self.spec, self.database, [])
        self.assertEqual(value['unresolved'], ['row-file:' + self.relative])

    def test_staging_or_orphan_file_is_unresolved(self):
        (self.file.parent / 'orphan').write_bytes(b'unresolved')
        value = authority.reconcile_material(self.spec, self.database, self.observe())
        self.assertTrue(any(item.startswith('unclassified-file:') for item in value['unresolved']))

    def test_original_database_session_or_prepared_writer_refuses(self):
        for key in ('sessions', 'prepared'):
            with self.subTest(key=key):
                value = copy.deepcopy(self.database)
                value[key] = [{'pid': 100}]
                with self.assertRaises(authority.Refused):
                    authority.reconcile_material(self.spec, value, self.observe())

    def test_database_oid_type_substitution_refuses(self):
        self.database['database_oid'] = float(self.database['database_oid'])
        with self.assertRaises(authority.Refused):
            authority.reconcile_material(self.spec, self.database, self.observe())

    def test_enabled_effects_remain_individually_unresolved(self):
        self.spec['manifest']['features'].update(tus=True, queue=True, multipart=True, redis=True)
        value = authority.reconcile_material(self.spec, self.database, self.observe())
        self.assertEqual({item for item in value['unresolved'] if item.startswith('pending-effect-barrier:')},
                         {'pending-effect-barrier:' + item for item in ('tus', 'queue', 'multipart', 'redis')})

    def test_unsafe_original_object_name_refuses(self):
        self.database['objects'][0]['name'] = '../escape'
        with self.assertRaises(authority.Refused):
            authority.reconcile_material(self.spec, self.database, self.observe())

    def test_duplicate_original_row_refuses(self):
        self.database['objects'].append(copy.deepcopy(self.database['objects'][0]))
        with self.assertRaises(authority.Refused):
            authority.reconcile_material(self.spec, self.database, self.observe())

    def test_direct_reconcile_running_writer_refuses_before_file_or_sql_read(self):
        # These are source-only observations and private-file material. The
        # patched guard grants no original service or native proof authority.
        files = self.observe()
        ledger_root = self.root / 'private-owner'
        ledger_root.mkdir(mode=0o700)
        local = authority.LocalNativeAuthority(self.spec, ledger_root)
        with local.ledger.locked() as ledger:
            ledger.append(self.spec, 'intent', '1' * 64)
            ledger.append(self.spec, 'observed', '1' * 64)
        cid = self.spec['manifest']['writers'][0]['container']
        running = {'writers': [{'container': cid, 'processes': copy.deepcopy(self.spec['processes'][cid]),
                                'stopped': False}], 'native_admitted': False,
                   'pending': list(authority.PENDING), 'observation_sha256': '1' * 64,
                   'database': copy.deepcopy(self.spec['database_incarnation'])}
        with patch('storage_native_authority._installed_launch_guard', return_value={}), \
             patch.object(local, 'observe', return_value=running), \
             patch('storage_native_authority.snapshot_files', return_value=files) as snapshots, \
             patch.object(local.commands, 'storage_rows', return_value=self.database) as sql:
            with self.assertRaises(authority.Refused):
                local.reconcile()
            snapshots.assert_not_called()
            sql.assert_not_called()

    def test_direct_reconcile_changed_stopped_observation_refuses_before_return(self):
        # A byte-perfect private snapshot cannot cover a changed original
        # writer observation. This checks the direct API, not drill dispatch.
        files = self.observe()
        ledger_root = self.root / 'private-owner'
        ledger_root.mkdir(mode=0o700)
        local = authority.LocalNativeAuthority(self.spec, ledger_root)
        with local.ledger.locked() as ledger:
            ledger.append(self.spec, 'intent', '1' * 64)
            ledger.append(self.spec, 'observed', '1' * 64)
        cid = self.spec['manifest']['writers'][0]['container']
        before = {'writers': [{'container': cid, 'processes': [], 'stopped': True}],
                  'native_admitted': False, 'pending': list(authority.PENDING),
                  'observation_sha256': '1' * 64, 'database': copy.deepcopy(self.spec['database_incarnation'])}
        after = copy.deepcopy(before)
        after['observation_sha256'] = '2' * 64
        with patch('storage_native_authority._installed_launch_guard', return_value={}), \
             patch.object(local, 'observe', side_effect=[before, after]), \
             patch('storage_native_authority.snapshot_files', return_value=files), \
             patch.object(local.commands, 'storage_rows', return_value=self.database):
            with self.assertRaises(authority.Refused):
                local.reconcile()

    def test_direct_reconcile_missing_observed_stop_refuses_before_reads(self):
        ledger_root = self.root / 'private-owner'
        ledger_root.mkdir(mode=0o700)
        local = authority.LocalNativeAuthority(self.spec, ledger_root)
        with patch('storage_native_authority._installed_launch_guard', return_value={}), \
             patch.object(local, 'observe') as observer, \
             patch('storage_native_authority.snapshot_files') as snapshots, \
             patch.object(local.commands, 'storage_rows') as sql:
            with self.assertRaises(authority.Refused):
                local.reconcile()
            observer.assert_not_called()
            snapshots.assert_not_called()
            sql.assert_not_called()

    def test_direct_reconcile_foreign_epoch_owned_stop_refuses_before_reads(self):
        ledger_root = self.root / 'private-owner'
        ledger_root.mkdir(mode=0o700)
        local = authority.LocalNativeAuthority(self.spec, ledger_root)
        foreign = copy.deepcopy(self.spec)
        foreign['manifest']['management_epoch'] += 1
        with local.ledger.locked() as ledger:
            ledger.append(foreign, 'intent', '1' * 64)
            ledger.append(foreign, 'observed', '1' * 64)
        with patch('storage_native_authority._installed_launch_guard', return_value={}), \
             patch.object(local, 'observe') as observer, \
             patch('storage_native_authority.snapshot_files') as snapshots:
            with self.assertRaises(authority.Refused):
                local.reconcile()
            observer.assert_not_called()
            snapshots.assert_not_called()

    def test_direct_reconcile_cached_observer_mutation_is_detached_and_refused(self):
        files = self.observe()
        ledger_root = self.root / 'private-owner'
        ledger_root.mkdir(mode=0o700)
        local = authority.LocalNativeAuthority(self.spec, ledger_root)
        with local.ledger.locked() as ledger:
            ledger.append(self.spec, 'intent', '1' * 64)
            ledger.append(self.spec, 'observed', '1' * 64)
        cid = self.spec['manifest']['writers'][0]['container']
        cached = {'writers': [{'container': cid, 'processes': [], 'stopped': True}],
                  'native_admitted': False, 'pending': list(authority.PENDING),
                  'observation_sha256': '1' * 64, 'database': copy.deepcopy(self.spec['database_incarnation'])}
        def private_rows():
            cached['observation_sha256'] = '2' * 64
            return copy.deepcopy(self.database)
        with patch('storage_native_authority._installed_launch_guard', return_value={}), \
             patch.object(local, 'observe', return_value=cached), \
             patch('storage_native_authority.snapshot_files', return_value=files), \
             patch.object(local.commands, 'storage_rows', side_effect=private_rows):
            with self.assertRaises(authority.Refused):
                local.reconcile()


class OriginalProtocolSourceTests(unittest.TestCase):
    def test_sigv4_matches_published_aws_s3_vector(self):
        # Published synthetic keys, not workstation credentials.
        headers = authority.sign_s3_request('GET', '/test.txt', [],
            {'host': 'examplebucket.s3.amazonaws.com', 'range': 'bytes=0-9'}, b'',
            'AKIAIOSFODNN7EXAMPLE', 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY',
            'us-east-1', '20130524T000000Z')
        self.assertTrue(headers['authorization'].endswith(
            'Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41'))

    def test_sigv4_query_encodes_and_sorts_before_signing(self):
        self.assertEqual(authority.s3_query([('z', '+ /'), ('a', 'z'), ('a', 'a')]),
                         'a=a&a=z&z=%2B%20%2F')

    def test_signer_refuses_header_injection_and_unhashed_payload(self):
        for headers, body in [({'host': 'x\r\nevil: 1'}, b''), ({'host': 'x'}, 'text')]:
            with self.assertRaises(authority.Refused):
                authority.sign_s3_request('PUT', '/x', [], headers, body,
                                          'unit', 'private-unit', 'local', '20130524T000000Z')

    def test_xml_requires_expected_root_and_forbids_entities(self):
        for data in [b'<Error><Code>Bad</Code></Error>',
                     b'<!DOCTYPE x [<!ENTITY a "value">]><ListPartsResult>&a;</ListPartsResult>']:
            with self.assertRaises(authority.Refused):
                authority.s3_xml(data, 'ListPartsResult')

    def test_multipart_parts_detects_truncation_wrong_upload_and_etag(self):
        good = b'<ListPartsResult><Bucket>unit</Bucket><Key>x</Key><UploadId>u</UploadId><IsTruncated>false</IsTruncated><Part><PartNumber>1</PartNumber><ETag>"abc"</ETag></Part></ListPartsResult>'
        expected = [{'number': 1, 'etag': '"abc"'}]
        authority.verify_s3_parts(good, 'unit', 'x', 'u', expected)
        for old, new in [(b'false', b'true'), (b'<UploadId>u', b'<UploadId>other'),
                         (b'"abc"', b'"other"'), (b'<PartNumber>1', b'<PartNumber>2')]:
            with self.assertRaises(authority.Refused):
                authority.verify_s3_parts(good.replace(old, new), 'unit', 'x', 'u', expected)

    def test_live_http_and_multipart_refuse_before_secret_read_or_socket(self):
        spec, _ = candidate()
        with patch('storage_native_authority.read_protocol_credentials', side_effect=AssertionError('No secret read')), \
             patch('socket.create_connection', side_effect=AssertionError('No socket')):
            transport = authority.OriginalHttpTransport(spec, 'http://127.0.0.1:8000',
                'tenant.unit', '/private/source-only.json')
            for action in [lambda: transport.disconnect('unit', 'x'),
                           lambda: transport.s3('POST', 'unit', 'x', [('uploads', '')], b''),
                           lambda: authority.exercise_multipart(transport, 'unit', 'x')]:
                with self.assertRaisesRegex(authority.Refused, 'Installed'):
                    action()

    def test_private_protocol_credentials_refuse_symlink_and_open_modes(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'credentials.json'
            path.write_text('{"service_key":"unit","access_key":"unit","secret_key":"private-unit","region":"local"}')
            os.chmod(path, 0o644)
            with self.assertRaises(authority.Refused):
                authority.read_protocol_credentials(str(path))
            os.chmod(path, 0o600)
            linked = Path(root) / 'linked'; linked.symlink_to(path)
            with self.assertRaises(authority.Refused):
                authority.read_protocol_credentials(str(linked))
            self.assertEqual(authority.read_protocol_credentials(str(path))['region'], 'local')

    def test_http_assignment_rejects_redirect_targets_and_header_injection(self):
        spec, _ = candidate()
        for endpoint, host in [('http://127.0.0.1:8000/path', 'tenant.unit'),
                               ('http://user@127.0.0.1:8000', 'tenant.unit'),
                               ('http://external.invalid:8000', 'tenant.unit'),
                               ('http://127.0.0.1:8000', 'x\r\nevil:1')]:
            with self.assertRaises(authority.Refused):
                authority.OriginalHttpTransport(spec, endpoint, host, '/private/source-only.json')


    def test_multipart_roundtrip_requires_exact_part_listing_and_assembled_bytes(self):
        spec, _ = candidate()
        transport = authority.OriginalHttpTransport(spec, 'http://127.0.0.1:8000',
            'tenant.unit', '/private/source-only.json')
        first, last = b'\x25' * (5 * 1024 * 1024), b'\x5b' * 65536
        etags = ['"' + hashlib.md5(value, usedforsecurity=False).hexdigest() + '"' for value in (first, last)]
        calls = []
        def original_request(method, bucket, key, pairs, body, **kwargs):
            calls.append((method, pairs))
            self.assertEqual((bucket, key), ('unit', 'x'))
            index = len(calls)
            if index == 1:
                return b'<InitiateMultipartUploadResult><Bucket>unit</Bucket><Key>x</Key><UploadId>u</UploadId></InitiateMultipartUploadResult>', None
            if index in (2, 3):
                self.assertEqual(method, 'PUT')
                self.assertEqual(pairs, [('uploadId', 'u'), ('partNumber', str(index - 1))])
                self.assertEqual(body, (first, last)[index - 2])
                return b'', etags[index - 2]
            if index == 4:
                parts = ''.join('<Part><PartNumber>' + str(i + 1) + '</PartNumber><ETag>' + tag + '</ETag></Part>' for i, tag in enumerate(etags))
                return ('<ListPartsResult><Bucket>unit</Bucket><Key>x</Key><UploadId>u</UploadId><IsTruncated>false</IsTruncated>' + parts + '</ListPartsResult>').encode(), None
            if index == 5:
                self.assertEqual(kwargs['content_type'], 'application/xml')
                self.assertIn(etags[0].encode(), body)
                return b'<CompleteMultipartUploadResult><Bucket>unit</Bucket><Key>x</Key></CompleteMultipartUploadResult>', None
            self.assertEqual(kwargs['max_response'], len(first) + len(last))
            return first + last, None
        with patch.object(transport, 's3', side_effect=original_request):
            result = authority.exercise_multipart(transport, 'unit', 'x')
        self.assertEqual(len(calls), 6)
        self.assertFalse(result['native_admitted'])
        self.assertIn('actual-versioned-files', result['pending'])

    def test_multipart_error_retains_ambiguity_without_abort_or_retry(self):
        spec, _ = candidate()
        transport = authority.OriginalHttpTransport(spec, 'http://127.0.0.1:8000',
            'tenant.unit', '/private/source-only.json')
        created = b'<InitiateMultipartUploadResult><Bucket>unit</Bucket><Key>x</Key><UploadId>u</UploadId></InitiateMultipartUploadResult>'
        with patch.object(transport, 's3', side_effect=[(created, None), authority.Refused('Uncertain delivery')]) as command:
            with self.assertRaisesRegex(authority.Refused, 'Uncertain delivery'):
                authority.exercise_multipart(transport, 'unit', 'x')
            self.assertEqual(command.call_count, 2)
            self.assertEqual(command.call_args[0][0], 'PUT')

    def test_s3_response_error_or_truncation_never_becomes_success(self):
        spec, _ = candidate()
        transport = authority.OriginalHttpTransport(spec, 'http://127.0.0.1:8000',
            'tenant.unit', '/private/source-only.json')
        for status, payload in [(302, b''), (500, b''), (200, b'toolong')]:
            response = unittest.mock.Mock()
            response.status = status
            response.read1.side_effect = [payload, b'']
            connection = unittest.mock.Mock(); connection.getresponse.return_value = response
            with patch.object(authority, '_installed_launch_guard', return_value={}), \
                 patch.object(authority, 'read_protocol_credentials', return_value={
                    'service_key':'unit', 'access_key':'unit', 'secret_key':'private-unit', 'region':'local'}), \
                 patch('http.client.HTTPConnection', return_value=connection), \
                 patch.object(authority, '_protocol_deadline', return_value=__import__('contextlib').nullcontext()):
                with self.assertRaises(authority.Refused):
                    transport.s3('GET', 'unit', 'x', [], b'', max_response=3)
                connection.close.assert_called_once()


class ControllerFaultSourceTests(unittest.TestCase):
    def test_fault_launch_refuses_before_fork(self):
        spec, _ = candidate()
        with tempfile.TemporaryDirectory() as root, patch('os.fork', side_effect=AssertionError('No process')):
            os.chmod(root, 0o700)
            local = authority.LocalNativeAuthority(spec, root)
            for boundary in ('after-intent', 'after-stop-before-ack'):
                with self.assertRaisesRegex(authority.Refused, 'Installed'):
                    local.run_stop_fault(boundary)

    def test_stop_fault_follows_fsynced_intent_and_precise_effect_boundary(self):
        spec, material = candidate()
        observation = authority.decode_inventory(spec, material)
        class Killed(Exception):
            pass
        for boundary, effects in [('after-intent', 0), ('after-stop-before-ack', 1)]:
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as root:
                os.chmod(root, 0o700)
                local = authority.LocalNativeAuthority(spec, root)
                def kill(pid, signum):
                    self.assertEqual(pid, os.getpid())
                    self.assertEqual(signum, 9)
                    # Read only after the exact original ledger append fsync.
                    records = [json.loads(line) for line in (Path(root) / 'owner.jsonl').read_text().splitlines()]
                    self.assertEqual([row['phase'] for row in records], ['intent'])
                    raise Killed()
                with patch.object(authority, '_installed_launch_guard', return_value={}), \
                     patch.object(local, 'observe', return_value=observation), \
                     patch.object(local.commands, 'stop') as stop, patch('os.kill', side_effect=kill):
                    with self.assertRaises(Killed):
                        local.stop(fault_boundary=boundary)
                    self.assertEqual(stop.call_count, effects)

    def test_unknown_fault_cannot_persist_intent_or_run_original_command(self):
        spec, _ = candidate()
        with tempfile.TemporaryDirectory() as root:
            os.chmod(root, 0o700)
            local = authority.LocalNativeAuthority(spec, root)
            with patch.object(authority, '_installed_launch_guard', return_value={}), \
                 patch.object(local.commands, 'stop', side_effect=AssertionError('No original effect')):
                with self.assertRaises(authority.Refused):
                    local.stop(fault_boundary='after-ack')
            self.assertFalse((Path(root) / 'owner.jsonl').exists())


if __name__ == '__main__':
    unittest.main()
