"""Offline negative fixtures. No registry, git network or Docker daemon calls."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('reference_bundle', Path(__file__).resolve().parents[1] / 'deploy/verify/reference_bundle.py')
reference = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reference)


def packet():
    config = 'sha256:' + 'a' * 64
    raw = json.dumps({'config': {'digest': config}})
    digest = 'sha256:' + reference.sha(raw.encode())
    image = {'reference': 'public/image:v1', 'digest': digest, 'manifest': raw, 'errors': [],
             'platforms': {p: {'manifest': raw, 'manifest_digest': digest, 'config_digest': config, 'os':p.split('/')[0], 'architecture':p.split('/')[1]}
                           for p in reference.PLATFORMS}}
    return {'schema': 1, 'repository': reference.REPOSITORY, 'commit': reference.COMMIT, 'tag':reference.TAG, 'tag_object':reference.TAG_OBJECT,
            'files': [{'path': 'docker/docker-compose.yml', 'mode':'100644', 'sha256':reference.CORE_HASH, 'git_blob':'b'*40, 'bytes':21240}],
            'compose': {'images': ['public/image:v1'], 'errors': []}, 'images': [image]}


class ReferenceBundleTest(unittest.TestCase):
    def test_complete_metadata_fixture(self):
        self.assertEqual(reference.packet_errors(packet()), [])

    def test_missing_platform_config_digest_refused(self):
        data = packet()
        del data['images'][0]['platforms']['linux/arm64']['config_digest']
        self.assertTrue(reference.packet_errors(data))

    def test_mutable_tag_without_manifest_refused(self):
        data = packet()
        del data['images'][0]['digest']
        self.assertTrue(reference.packet_errors(data))

    def test_manifest_tampering_refused(self):
        data = packet()
        data['images'][0]['manifest'] += ' '
        self.assertTrue(reference.packet_errors(data))

    def test_missing_duplicate_image_refused(self):
        for images in ([], [packet()['images'][0]] * 2):
            data = packet()
            data['images'] = images
            self.assertTrue(reference.packet_errors(data))

    def test_source_symlink_refused_before_blob_read(self):
        calls = []
        def runner(command, env, diagnostics=None):
            calls.append(command)
            if 'ls-remote' in command:
                return (reference.TAG_OBJECT + '\trefs/tags/' + reference.TAG + '\n'
                        + reference.COMMIT + '\trefs/tags/' + reference.TAG + '^{}\n').encode()
            if 'rev-parse' in command:
                return (reference.COMMIT + '\n').encode()
            if 'ls-tree' in command:
                return b'120000 blob aaaa\tdocker/symlink\0'
            return b''
        with tempfile.TemporaryDirectory() as temp, patch.object(reference, 'run', runner):
            with self.assertRaisesRegex(reference.Refusal, 'symlink'):
                reference.source(Path(temp), {})
        self.assertFalse(any('cat-file' in c for c in calls))

    def test_all_compose_files_and_environment_refs_retained(self):
        rows = [{'path': path} for path in ('docker/docker-compose.yml', 'docker/dev/docker-compose.dev.yml',
                                          'docker/tests/docker-compose.s3.test.yml')]
        def runner(command, env, diagnostics=None):
            if '--images' in command:
                return b'${UNSET_IMAGE}\n'
            return json.dumps({'services': {'optional': {'image': '${UNSET_IMAGE}'}}}).encode()
        with patch.object(reference, 'run', runner):
            inventory = reference.compose_inventory(Path('/public'), rows, {})
        self.assertEqual(len(inventory['documents']), 3)
        self.assertEqual(len(inventory['errors']), 3)
        self.assertEqual(inventory['images'], [])

    def test_unknown_yaml_schema_rejection_refused(self):
        with patch.object(reference, 'run', side_effect=reference.Refusal('services.api invalid property')):
            inventory = reference.compose_inventory(Path('/public'), [{'path':'docker/optional/search.yml'}], {})
        self.assertTrue(inventory['errors'])
        self.assertEqual(inventory['auxiliary_yaml'], [])

    def test_corrupt_known_auxiliary_refused(self):
        path = next(iter(reference.AUXILIARY))
        with patch.object(reference, 'run', side_effect=AssertionError('must not parse corrupted auxiliary')):
            inventory = reference.compose_inventory(Path('/public'), [{'path':path,'sha256':'0'*64}], {})
        self.assertIn('known auxiliary checksum differs', inventory['errors'][0])

    def test_overlay_inherited_image_is_explicit(self):
        def runner(command, env, diagnostics=None):
            if '--images' in command:
                return b'reference-inherited\npublic/image:v1\n'
            return b'{"services":{"inherited":{},"optional":{"image":"public/image:v1"}}}'
        with patch.object(reference, 'run', runner):
            inventory = reference.compose_inventory(Path('/public'), [{'path':'docker/optional/search.yml'}], {})
        self.assertEqual(inventory['errors'], [])
        self.assertEqual(inventory['images'], ['public/image:v1'])
        self.assertIsNone(inventory['documents'][0]['services']['inherited']['image'])

    def test_build_only_image_unfreezable(self):
        def runner(command, env, diagnostics=None):
            if '--images' in command:
                return b'reference-studio\n'
            return b'{"services":{"studio":{"build":{"context":"../../studio"}}}}'
        with patch.object(reference, 'run', runner):
            inventory = reference.compose_inventory(Path('/public'), [{'path':'docker/dev/docker-compose.dev.yml'}], {})
        self.assertIn('build-only service image cannot be frozen', inventory['errors'][0])

    def test_native_image_disagreement_refused(self):
        def runner(command, env, diagnostics=None):
            if '--images' in command:
                return b'other:v1\n'
            return b'{"services":{"core":{"image":"actual:v1"}}}'
        with patch.object(reference, 'run', runner):
            inventory = reference.compose_inventory(Path('/public'), [{'path':'docker/docker-compose.yml'}], {})
        self.assertIn('native image inventory mismatch', inventory['errors'][0])

    def test_missing_or_ambiguous_platform_explicit(self):
        descriptor = {'digest': 'sha256:' + 'a' * 64}
        raw = json.dumps({'manifests': [{'platform': {'os':'linux','architecture':'amd64'},
                                        'digest':'sha256:'+'b'*64}] * 2}).encode()
        descriptor['digest'] = 'sha256:' + reference.sha(raw)
        def runner(command, env, diagnostics=None):
            return raw if '--raw' in command else json.dumps(descriptor).encode()
        with patch.object(reference, 'run', runner):
            image = reference.inspect_image('public/image:v1', {})
        self.assertIn('2 matches', image['platforms']['linux/amd64']['error'])
        self.assertIn('0 matches', image['platforms']['linux/arm64']['error'])

    def test_registry_error_retained_without_retry(self):
        with patch.object(reference, 'run', side_effect=reference.Refusal('429 registry rate limit')) as runner:
            image = reference.inspect_image('public/image:v1', {})
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(image['errors'], ['429 registry rate limit'])

    def test_incomplete_packet_refuses_without_external_commands(self):
        data = packet()
        del data['images'][0]['platforms']['linux/arm64']['config_digest']
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle, report = root/'bundle.json', root/'report.json'
            bundle.write_text(json.dumps(data))
            with patch.object(reference, 'run', side_effect=AssertionError('no external commands')):
                self.assertEqual(reference.main(['verify','--bundle',str(bundle),'--output',str(report)]), 1)
            self.assertIn('not run', json.loads(report.read_text())['source_refetch'])

    def test_verify_source_tampering_nonzero(self):
        data = packet()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle, report = root/'bundle.json', root/'report.json'
            bundle.write_text(json.dumps(data))
            with patch.object(reference, 'source', return_value=(root, [{'path':'docker/changed'}])), \
                 patch.object(reference, 'compose_inventory', return_value=data['compose']), \
                 patch.object(reference, 'inspect_image', side_effect=AssertionError('must refuse before registry')):
                self.assertEqual(reference.main(['verify','--bundle',str(bundle),'--output',str(report)]), 1)
            self.assertIn('fresh pinned source inventory differs', json.loads(report.read_text())['errors'])


if __name__ == '__main__':
    unittest.main()
