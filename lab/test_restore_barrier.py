import copy
import unittest

from restore_barrier import BarrierError, admit_storage


CID = 'a' * 64
IMAGE = 'sha256:' + 'b' * 64
OPTIONS = {'name': 'owned-storage', 'owner': 'durable-upstream', 'image_id': IMAGE,
           'volume': 'owned-objects', 'tenant_parent': 'sbarbase-lab'}


def record():
    return {'Id': CID, 'Name': '/owned-storage', 'Image': IMAGE,
            'Config': {'Labels': {'io.sbarbase.owner': 'durable-upstream'},
                       'Env': ['STORAGE_BACKEND=file', 'GLOBAL_S3_BUCKET=sbarbase-lab',
                               'FILE_STORAGE_BACKEND_PATH=/tmp/storage-data', 'MULTI_TENANT=true',
                               'S3_PROTOCOL_ENABLED=false', 'PG_QUEUE_ENABLE=false', 'PRIVATE=password']},
            'State': {'Running': True, 'Paused': False, 'Restarting': False},
            'Mounts': [{'Type': 'volume', 'Name': 'owned-objects', 'Destination': '/tmp/storage-data', 'RW': True}]}


class SharedStorageBarrierTests(unittest.TestCase):
    def test_exact_native_cid_before_stop_and_held_stopped_proof(self):
        current = record()
        self.assertEqual(admit_storage(current, **OPTIONS), CID)
        with self.assertRaises(BarrierError):
            admit_storage(current, **OPTIONS, container_id=CID, stopped=True)
        current['State']['Running'] = False
        self.assertEqual(admit_storage(current, **OPTIONS, container_id=CID, stopped=True), CID)

    def test_replacement_owner_image_and_nonfile_backend_refuse_privately(self):
        cases = []
        for key, value in [('Id', 'c' * 64), ('Name', '/other'), ('Image', 'sha256:' + 'c' * 64)]:
            current = record()
            current[key] = value
            cases.append(current)
        current = record()
        current['Config']['Labels']['io.sbarbase.owner'] = 'other'
        cases.append(current)
        current = record()
        current['Config']['Env'][0] = 'STORAGE_BACKEND=s3'
        cases.append(current)
        current = record()
        current['Config']['Env'].append('STORAGE_BACKEND=file')
        cases.append(current)
        for current in cases:
            with self.subTest(record=current), self.assertRaises(BarrierError) as error:
                admit_storage(current, **OPTIONS, container_id=CID)
            self.assertNotIn('password', str(error.exception))

    def test_volume_identity_and_paused_or_restarting_are_not_stopped_proof(self):
        current = record()
        current['State']['Running'] = False
        for key, value in [('Paused', True), ('Restarting', True), ('Running', 'false')]:
            changed = copy.deepcopy(current)
            changed['State'][key] = value
            with self.subTest(state=key), self.assertRaises(BarrierError):
                admit_storage(changed, **OPTIONS, stopped=True)
        for key, value in [('Name', 'foreign'), ('Type', 'bind'), ('RW', False)]:
            changed = copy.deepcopy(current)
            changed['Mounts'][0][key] = value
            with self.subTest(mount=key), self.assertRaises(BarrierError):
                admit_storage(changed, **OPTIONS, stopped=True)


if __name__ == '__main__':
    unittest.main()
