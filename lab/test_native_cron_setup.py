"""Closed Cron setup admission using captured public original-image metadata."""

import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy' / 'verify'))
from native_worker_inspect import admit_cron_setup

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'native_cron_setup.json'


def captured():
    return json.loads(FIXTURE.read_text())


def admit(value, installed=False):
    return admit_cron_setup(value['after'] if installed else value['before'], value['after'],
                            value['identity'], newly_installed=not installed,
                            provenance=value['provenance'])


class CronSetupTests(unittest.TestCase):
    def test_captured_original_new_and_existing_surface(self):
        for installed in (False, True):
            result = admit(captured(), installed)
            self.assertEqual(len(result['routine_oids']), 7)
            self.assertEqual(len(result['relation_oids']), 7)

    def test_every_routine_requires_exact_vendor_grant_and_nonnull_acl(self):
        original = captured()
        for index, row in enumerate(original['after']['routines']):
            if row['namespace'] != 'cron':
                continue
            for mutation in ('grantor', 'grantee', 'grantable', 'missing', 'extra', 'null', 'raw'):
                value = copy.deepcopy(original)
                routine = value['after']['routines'][index]
                grant = next(g for g in routine['acl'] if g['grantee'] == '16384')
                if mutation == 'grantor':
                    grant['grantor'] = '16384'
                elif mutation == 'grantee':
                    grant['grantee'] = '0'
                elif mutation == 'grantable':
                    grant['is_grantable'] = False
                elif mutation == 'missing':
                    routine['acl'].remove(grant)
                    routine['acl_raw'] = [a for a in routine['acl_raw'] if not a.startswith('postgres=')]
                elif mutation == 'extra':
                    routine['acl'].append({'grantor': '16384', 'grantee': '10', 'privilege_type': 'EXECUTE', 'is_grantable': False})
                elif mutation == 'null':
                    routine['acl_raw'] = None
                else:
                    routine['acl_raw'] = [a.replace('postgres=X*', 'postgres=X') for a in routine['acl_raw']]
                with self.subTest(routine=row['name'], mutation=mutation), self.assertRaises(RuntimeError):
                    admit(value)

    def test_closed_metadata_and_restricted_public_revokes(self):
        for mutation in ('signature', 'symbol', 'library', 'owner', 'global', 'old', 'public'):
            value = captured()
            routine = next(r for r in value['after']['routines'] if r['namespace'] == 'cron' and r['name'] == 'alter_job')
            if mutation == 'signature':
                routine['argument_types'] += ', text'
            elif mutation == 'symbol':
                routine['c_symbol'] += '_wrong'
            elif mutation == 'library':
                routine['library'] = '$libdir/other'
            elif mutation == 'owner':
                routine['owner_oid'] = '16384'
            elif mutation == 'global':
                value['after']['creation_defaults'][0]['global_acl_raw'] = []
            elif mutation == 'old':
                value['after']['extensions'][0]['version'] += '_wrong'
            else:
                routine['acl'].append({'grantor': '10', 'grantee': '0', 'privilege_type': 'EXECUTE', 'is_grantable': False})
                routine['acl_raw'].append('=X/supabase_admin')
            with self.subTest(mutation=mutation), self.assertRaises(RuntimeError):
                admit(value)

    def test_witness_revalidates_bytes_phases_and_catalog_binding(self):
        for mutation in ('absent', 'path', 'image', 'source', 'hash', 'bytes', 'missing_phase',
                         'failed_phase', 'hook_body', 'hook_owner', 'trigger', 'setting', 'old_metadata', 'setup_drift', 'upstream', 'phase_hash',
                         'phase_bytes', 'phase_source', 'observation_mismatch', 'typed_observation'):
            value = captured()
            witness = value['provenance']
            if mutation == 'absent':
                value['provenance'] = None
            elif mutation == 'path':
                witness['file']['path'] += '.wrong'
            elif mutation == 'image':
                witness['image_id'] = 'sha256:' + '0' * 64
            elif mutation == 'source':
                witness['file']['source'] = 'x' + witness['file']['source'][1:]
            elif mutation == 'hash':
                witness['file']['sha256'] = '0' * 64
            elif mutation == 'bytes':
                witness['file']['bytes'] += 1
            elif mutation == 'missing_phase':
                del witness['before']
            elif mutation == 'failed_phase':
                witness['setup']['passed'] = False
            elif mutation == 'hook_body':
                witness['before']['observation']['hooks'][0]['body'] += ' '
            elif mutation == 'hook_owner':
                witness['before']['observation']['hooks'][0]['owner_oid'] = '16384'
            elif mutation == 'trigger':
                witness['before']['observation']['triggers'][0]['enabled'] = 'D'
            elif mutation == 'setting':
                witness['before']['observation']['settings']['supautils.extension_custom_scripts_path'] = '/tmp'
            elif mutation == 'old_metadata':
                value['before']['routines'][0]['language'] = 'sql'
            elif mutation == 'upstream':
                witness['upstream_commit'] = '0' * 40
            elif mutation == 'phase_hash':
                witness['setup']['sha256'] = '0' * 64
            elif mutation == 'phase_bytes':
                witness['setup']['bytes'] += 1
            elif mutation == 'phase_source':
                witness['setup']['source'] += ' '
            elif mutation == 'observation_mismatch':
                witness['setup']['observation']['current_user'] = 'postgres'
            elif mutation == 'typed_observation':
                witness['setup']['observation']['hooks'][0]['security_definer'] = 0
            else:
                phase = witness['setup']
                phase['observation']['settings']['supautils.privileged_extensions'] += ', extra_extension'
                phase['source'] = json.dumps(phase['observation'])
                phase['bytes'] = len(phase['source'].encode('utf-8'))
                phase['sha256'] = hashlib.sha256(phase['source'].encode('utf-8')).hexdigest()
            with self.subTest(mutation=mutation), self.assertRaises(RuntimeError):
                admit(value)

    def test_existing_surface_binds_real_projection_before_private_recursion(self):
        value = captured()
        value['after']['routines'][0]['owner_oid'] = '16384'
        with self.assertRaises(RuntimeError):
            admit(value, installed=True)


if __name__ == '__main__':
    unittest.main()
