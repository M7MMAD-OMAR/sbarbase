import unittest
import json
from types import SimpleNamespace

import postgres_1711_preflight as preflight


class InventoryTests(unittest.TestCase):
    def test_empty_inventory(self):
        self.assertEqual(preflight.refusals(dict(ltree_indexes=0, float_gist_indexes=0,
                                                custom_estimators=0, pgcrypto=False)), [])

    def test_each_hazard_refuses(self):
        for key in ('ltree_indexes', 'float_gist_indexes', 'custom_estimators', 'pgcrypto'):
            inventory = dict(ltree_indexes=0, float_gist_indexes=0, custom_estimators=0, pgcrypto=False)
            inventory[key] = True if key == 'pgcrypto' else 1
            with self.subTest(key=key):
                self.assertEqual(len(preflight.refusals(inventory)), 1)

    def test_incomplete_or_spoofed_inventory_refuses(self):
        for inventory in ({}, dict(ltree_indexes=True, float_gist_indexes=0, custom_estimators=0, pgcrypto=False),
                          dict(ltree_indexes=0, float_gist_indexes=0, custom_estimators=0, pgcrypto=0)):
            with self.subTest(inventory=inventory), self.assertRaises(ValueError):
                preflight.refusals(inventory)

    def test_cluster_checks_every_connectable_database_and_is_read_only(self):
        databases = [{'name': 'postgres', 'connect': True, 'template': False},
                     {'name': 'template0', 'connect': False, 'template': True},
                     {'name': 'tenant', 'connect': True, 'template': False}]
        empty = dict(ltree_indexes=0, float_gist_indexes=0, custom_estimators=0, pgcrypto=False)
        calls = []
        def docker(*args, **kwargs):
            calls.append((args, kwargs))
            value = databases if len(calls) == 1 else empty
            return SimpleNamespace(returncode=0, stdout=json.dumps(value))
        preflight.inspect_cluster(docker, 'container')
        self.assertEqual([args[-1] for args, _ in calls], ['postgres', 'postgres', 'tenant'])
        self.assertTrue(all(k['data'].startswith('BEGIN READ ONLY;') for _, k in calls))

    def test_inaccessible_malformed_and_hazardous_clusters_refuse(self):
        valid = [{'name':'postgres','connect':True,'template':False},
                 {'name':'template0','connect':False,'template':True}]
        cases = [(valid + [{'name':'hidden','connect':False,'template':False}], None),
                 ([], None), (valid, {'pgcrypto':True}),
                 (valid, dict(ltree_indexes=0,float_gist_indexes=1,custom_estimators=0,pgcrypto=False))]
        for databases, result in cases:
            calls=[]
            def docker(*args, **kwargs):
                calls.append(args)
                return SimpleNamespace(returncode=0, stdout=json.dumps(databases if len(calls)==1 else result))
            with self.subTest(databases=databases,result=result), self.assertRaises(RuntimeError):
                preflight.inspect_cluster(docker,'container')


if __name__ == '__main__':
    unittest.main()
