"""The cluster level of a backup run: managed roles, their rules, and a record that never breaks a run."""
import json
import unittest
from unittest.mock import patch

import cluster_inventory as inventory

E = 'e_' + 'a' * 24
IMAGE = 'sha256:' + '1' * 64


def row(name, **changes):
    found = inventory.classify(name)
    values = {**dict(inventory.RULES[found[0]]), 'name': name, 'rolcanlogin': True, 'rolconnlimit': 4,
              'member_of': [], 'settings': []}
    if name == 'supabase_realtime_admin':
        values['rolcanlogin'] = False
    values.update(changes)
    return values


def roles():
    return [row(f'{E}_{kind}') for kind in ('auth', 'rest', 'storage', 'studio', 'realtime', 'developer')] + \
        [row(name) for name in inventory.CLUSTER_ROLES]


PARAMETERS = [{'parameter': 'log_min_messages', 'grantee': f'{E}_realtime', 'privilege': 'SET'},
              {'parameter': 'log_min_messages', 'grantee': 'supabase_realtime_admin', 'privilege': 'SET'}]


def everything(role_rows=None, parameters=PARAMETERS):
    return {'roles': roles() if role_rows is None else role_rows, 'parameters': parameters}


def grant(role, inherit=True, set_=True, admin=False):
    return {'role': role, 'inherit': inherit, 'set': set_, 'admin': admin}


# Written out by hand from how the runtime creates each role (lab/run.py, lab/durable_runtime.py,
# lab/studio.py), not derived from inventory.RULES: (inherit, replication, bypassrls).
EXPECTED = {'auth': (False, False, False), 'rest': (False, False, False), 'storage': (False, False, False),
            'studio': (False, False, True), 'developer': (True, False, True), 'realtime': (True, True, False),
            'storage_control': (False, False, False), 'management_auth': (False, False, False),
            'supabase_realtime_admin': (False, False, False)}


class InventoryTests(unittest.TestCase):
    def test_the_rules_are_the_attributes_the_runtime_gives(self):
        self.assertEqual(set(inventory.RULES), set(EXPECTED))
        for kind, (inherit, replication, bypass) in EXPECTED.items():
            with self.subTest(kind=kind):
                self.assertEqual(inventory.RULES[kind], {'rolsuper': False, 'rolinherit': inherit, 'rolcreaterole': False,
                                                         'rolcreatedb': False, 'rolreplication': replication,
                                                         'rolbypassrls': bypass})

    def test_a_role_with_its_kind_s_attributes_written_out_by_hand_passes_and_any_flip_is_refused(self):
        for kind, (inherit, replication, bypass) in EXPECTED.items():
            name = f'{E}_{kind}' if kind in ('auth', 'rest', 'storage', 'studio', 'developer', 'realtime') else kind
            values = {'name': name, 'rolsuper': False, 'rolinherit': inherit, 'rolcreaterole': False, 'rolcreatedb': False,
                      'rolreplication': replication, 'rolbypassrls': bypass,
                      'rolcanlogin': kind != 'supabase_realtime_admin', 'rolconnlimit': 4, 'member_of': [], 'settings': []}
            with self.subTest(kind=kind):
                self.assertEqual(inventory.from_rows(everything([values]), IMAGE)['refusals'], [])
                for key in inventory.ATTRIBUTES:
                    self.assertTrue(inventory.from_rows(everything([{**values, key: not values[key]}]), IMAGE)['refusals'], key)

    def test_every_managed_role_kept_to_its_rule_is_recorded_without_refusals(self):
        record = inventory.from_rows(everything(), IMAGE)
        self.assertEqual(record['refusals'], [])
        self.assertEqual(inventory.usable(record), [])
        kinds = {role['name']: (role['kind'], role['environment']) for role in record['roles']}
        self.assertEqual(kinds[f'{E}_realtime'], ('realtime', E))
        self.assertEqual(kinds['storage_control'], ('storage_control', None))
        self.assertEqual([role['name'] for role in record['roles']], sorted(kinds))

    def test_each_kind_keeps_its_own_attributes(self):
        # Realtime alone replicates; Studio and the developer login alone bypass row security; no superuser.
        for name, key, value in ((f'{E}_auth', 'rolreplication', True), (f'{E}_rest', 'rolbypassrls', True),
                                 (f'{E}_storage', 'rolinherit', True), (f'{E}_studio', 'rolinherit', True),
                                 (f'{E}_realtime', 'rolsuper', True), (f'{E}_developer', 'rolcreaterole', True),
                                 ('management_auth', 'rolcreatedb', True), (f'{E}_realtime', 'rolreplication', False)):
            with self.subTest(name=name, key=key):
                record = inventory.from_rows(everything([row(name, **{key: value})]), IMAGE)
                self.assertEqual(len(record['refusals']), 1)
                self.assertIn(key, record['refusals'][0])
                self.assertTrue(inventory.usable(record))

    def test_the_realtime_admin_never_logs_in_and_others_may_open_and_close(self):
        record = inventory.from_rows(everything([row('supabase_realtime_admin', rolcanlogin=True)]), IMAGE)
        self.assertIn('can log in', record['refusals'][0])
        for kind in ('studio', 'developer', 'realtime'):
            self.assertEqual(inventory.from_rows(everything([row(f'{E}_{kind}', rolcanlogin=False)]), IMAGE)['refusals'], [])

    def test_an_unexpected_row_is_refused_not_recorded(self):
        record = inventory.from_rows(everything([{'name': 'someone'}, 'junk']), IMAGE)
        self.assertEqual(record['roles'], [])
        self.assertEqual(len(record['refusals']), 2)

    def test_memberships_and_settings_are_recorded_in_order(self):
        record = inventory.from_rows(everything([row(f'{E}_realtime', member_of=[grant('service_role', False), grant('anon', False)],
                                                     settings=['search_path=_realtime'])]), IMAGE)
        role = record['roles'][0]
        self.assertEqual(record['refusals'], [])
        self.assertEqual((role['member_of'], role['settings']),
                         ([grant('anon', False), grant('service_role', False)], ['search_path=_realtime']))
        self.assertEqual(record['parameters'], PARAMETERS)

    def test_grant_options_and_value_types_are_checked(self):
        for changes, reason in (({'member_of': ['anon']}, 'malformed grants'),
                                ({'member_of': [{'role': 'anon', 'inherit': 1, 'set': True, 'admin': False}]}, 'malformed grants'),
                                ({'member_of': [{'role': 'anon'}]}, 'malformed grants'),
                                ({'settings': [1]}, 'malformed settings'), ({'rolconnlimit': '4'}, 'connection limit'),
                                ({'rolcanlogin': None}, 'login state'), ({'rolbypassrls': 0}, 'rolbypassrls')):
            with self.subTest(changes=changes):
                record = inventory.from_rows(everything([row(f'{E}_auth', **changes)]), IMAGE)
                self.assertTrue(any(reason in item for item in record['refusals']), record['refusals'])

    def test_malformed_parameter_privileges_are_refused(self):
        for parameters in (None, [{'parameter': 'x'}], [{'parameter': 1, 'grantee': 'a', 'privilege': 'SET'}]):
            record = inventory.from_rows(everything(parameters=parameters), IMAGE)
            self.assertIn('the parameter privileges are malformed', record['refusals'])

    def test_only_exact_managed_names_are_classified(self):
        for name in ('e_ab_auth', 'e_' + 'a' * 25 + '_auth', 'e_' + 'A' * 24 + '_auth', f'{E}_admin', f'x{E}_auth',
                     'storage_control2', 'postgres'):
            with self.subTest(name=name):
                self.assertIsNone(inventory.classify(name))
                self.assertIn('unexpected row', inventory.from_rows(everything([{'name': name}]), IMAGE)['refusals'][0])

    def test_the_realtime_admin_login_state_must_be_exactly_false(self):
        for value in (None, 1, 'f'):
            self.assertTrue(inventory.role_refusals({**row('supabase_realtime_admin'), 'kind': 'supabase_realtime_admin',
                                                     'rolcanlogin': value}))

    def test_usable_refuses_a_record_missing_keys_contradicting_names_or_listing_a_role_twice(self):
        good = inventory.from_rows(everything(), IMAGE)
        self.assertEqual(inventory.usable(good), [])
        auth = next(role for role in good['roles'] if role['kind'] == 'auth')
        cases = [{key: value for key, value in good.items() if key != 'refusals'},
                 {key: value for key, value in good.items() if key != 'parameters'},
                 {**good, 'refusals': True}, {**good, 'refusals': 5}, {**good, 'refusals': [1]},
                 {**good, 'parameters': 'x'},
                 {**good, 'roles': [{**auth, 'kind': 'rest'}]},
                 {**good, 'roles': [{**auth, 'environment': 'e_' + 'b' * 24}]},
                 {**good, 'roles': [auth, auth]}]
        for record in cases:
            with self.subTest(record=str(record)[:80]):
                problems = inventory.usable(record)
                self.assertTrue(problems)
                self.assertTrue(all(isinstance(item, str) for item in problems))

    def test_a_role_whose_name_is_not_text_is_a_reason_not_an_error(self):
        good = inventory.from_rows(everything(), IMAGE)
        for name in (['x'], {'a': 1}, None, 5):
            with self.subTest(name=name):
                problems = inventory.usable({**good, 'roles': [{**good['roles'][0], 'name': name}]})
                self.assertIn('a recorded role has no name', problems)

    def test_a_grant_role_must_be_text_and_a_connection_limit_a_plain_integer(self):
        bad_grant = {'role': 1, 'inherit': True, 'set': True, 'admin': False}
        for changes, reason in (({'member_of': [bad_grant]}, 'malformed grants'),
                                ({'rolconnlimit': True}, 'connection limit'), ({'rolconnlimit': 4.0}, 'connection limit')):
            with self.subTest(changes=changes):
                record = inventory.from_rows(everything([row(f'{E}_auth', **changes)]), IMAGE)
                self.assertTrue(any(reason in item for item in record['refusals']), record['refusals'])

    def test_take_never_raises_whatever_the_resolution_or_the_query_throws(self):
        import subprocess
        import backup
        for error in (subprocess.TimeoutExpired('docker', 1), FileNotFoundError('docker'), OSError('x'), KeyError('id')):
            with self.subTest(error=type(error).__name__):
                with patch.object(backup, 'image_pin', return_value={}), patch.object(backup, 'resolve_image', side_effect=error), \
                        patch.object(backup, 'sql', side_effect=error):
                    record = inventory.take()
                self.assertIsNone(record['image'])
                self.assertIn('could not be taken', record['refusals'][0])

    def test_suffixed_names_are_not_managed_in_the_code_or_the_query(self):
        self.assertIsNone(inventory.classify(f'{E}_authx'))
        self.assertIn("(auth|rest|storage|studio|realtime|developer)$'", inventory.QUERY)

    def test_usable_checks_every_role_s_shape_and_rule_again(self):
        good = inventory.from_rows(everything(), IMAGE)
        role = good['roles'][0]
        extra = {**good, 'roles': [{**role, 'password': 'x'}]}
        missing = {**good, 'roles': [{key: value for key, value in role.items() if key != 'settings'}]}
        flipped = {**good, 'roles': [{**role, 'rolsuper': True}], 'refusals': []}
        for record in (extra, missing, flipped):
            self.assertTrue(inventory.usable(record))

    def test_the_query_joins_grants_the_way_round_that_lists_a_role_s_own_memberships(self):
        # member_of lists the roles r is granted (m.member = r), with each grant's options.
        self.assertIn('JOIN pg_roles p ON p.oid = m.roleid WHERE m.member = r.oid', inventory.QUERY)
        for fragment in ("'role', p.rolname", "'inherit', m.inherit_option", "'set', m.set_option",
                         "'admin', m.admin_option", "'rolcanlogin', r.rolcanlogin", "'rolconnlimit', r.rolconnlimit",
                         'FROM pg_parameter_acl a, aclexplode(a.paracl) x JOIN pg_roles r ON r.oid = x.grantee'):
            self.assertIn(fragment, inventory.QUERY)
        self.assertEqual(inventory.QUERY.count(inventory.ROLE_NAMES), 2, 'roles and parameter grantees use one filter')

    def test_the_query_reads_the_recorded_fields_and_only_role_wide_settings(self):
        for fragment in ("'rolconnlimit'", "'rolcanlogin'", 'pg_auth_members', 's.setrole = r.oid AND s.setdatabase = 0',
                         "'storage_control', 'management_auth', 'supabase_realtime_admin'",
                         '(auth|rest|storage|studio|realtime|developer)'):
            self.assertIn(fragment, inventory.QUERY)
        for key in inventory.ATTRIBUTES:
            self.assertIn(f"'{key}', r.{key}", inventory.QUERY)

    def test_capture_runs_the_query_and_never_raises(self):
        seen = []
        record = inventory.capture(lambda query: seen.append(query) or json.dumps(everything()), IMAGE)
        self.assertEqual(seen, [inventory.QUERY])
        self.assertEqual((record['image'], record['refusals']), (IMAGE, []))
        for failing in (lambda query: (_ for _ in ()).throw(RuntimeError('database statement failed')),
                        lambda query: 'not json', lambda query: '{}', lambda query: '[]'):
            record = inventory.capture(failing, IMAGE)
            self.assertEqual(record['roles'], [])
            self.assertIn('could not be taken', record['refusals'][0])
            self.assertTrue(inventory.usable(record))

    def test_the_query_never_reads_a_password(self):
        self.assertNotIn('rolpassword', inventory.QUERY)
        self.assertNotIn('pg_authid', inventory.QUERY)

    def test_a_record_without_image_roles_or_shape_is_not_usable(self):
        good = inventory.from_rows(everything(), IMAGE)
        self.assertEqual(inventory.usable(good), [])
        for broken in (None, {}, {**good, 'version': 2}, {**good, 'image': None}, {**good, 'image': 'postgres:17'},
                       {**good, 'roles': None}, {**good, 'roles': [{'name': f'{E}_auth'}]}):
            with self.subTest(broken=str(broken)[:60]):
                self.assertTrue(inventory.usable(broken))

    def test_take_records_the_image_the_lock_resolves_to_and_survives_a_failed_resolution(self):
        import backup
        with patch.object(backup, 'resolve_image', return_value=('ref', IMAGE)), patch.object(backup, 'image_pin', return_value={}), \
                patch.object(backup, 'sql', return_value=json.dumps(everything())):
            self.assertEqual(inventory.take()['image'], IMAGE)
        with patch.object(backup, 'resolve_image', side_effect=backup.BackupError('no daemon')), \
                patch.object(backup, 'image_pin', return_value={}), patch.object(backup, 'sql', return_value='[]'):
            record = inventory.take()
        self.assertIsNone(record['image'])
        self.assertIn('no database image', ' '.join(inventory.usable(record)))


if __name__ == '__main__':
    unittest.main()
