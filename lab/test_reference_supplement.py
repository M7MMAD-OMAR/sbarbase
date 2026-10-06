import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
import urllib.error
from unittest.mock import patch

from deploy.verify import reference_supplement as ref

IMAGE = 'application/vnd.oci.image.manifest.v1+json'
CONFIG = 'application/vnd.oci.image.config.v1+json'
INDEX = 'application/vnd.oci.image.index.v1+json'


class SupplementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def save(self, name, raw):
        (self.root / name).write_bytes(raw)
        return {'path': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}

    def image(self):
        platforms, children = {}, []
        for arch in ['amd64', 'arm64']:
            config = json.dumps({'os': 'linux', 'architecture': arch}).encode()
            manifest = json.dumps({'schemaVersion': 2, 'mediaType': IMAGE,
                'config': {'mediaType': CONFIG, 'digest': 'sha256:' + ref.sha(config),
                           'size': len(config)}, 'layers': []}).encode()
            platforms['linux/' + arch] = {'config': self.save(arch + '.config', config),
                                        'manifest': self.save(arch + '.manifest', manifest)}
            children.append({'platform': {'os': 'linux', 'architecture': arch},
                             'mediaType': IMAGE, 'size': len(manifest),
                             'digest': 'sha256:' + ref.sha(manifest)})
        raw = json.dumps({'schemaVersion': 2, 'mediaType': INDEX, 'manifests': children}).encode()
        return {'reference': 'fixture/image:v1', 'root': self.save('index', raw),
                'platforms': platforms, 'provenance': {'state': 'unverified', 'evidence': []}}

    def replace_root(self, row, change):
        data = json.loads((self.root / 'index').read_bytes())
        change(data)
        row['root'] = self.save('index', json.dumps(data).encode())

    def replace_child(self, row, arch, change):
        data = json.loads((self.root / (arch + '.manifest')).read_bytes())
        change(data)
        raw = json.dumps(data).encode()
        row['platforms']['linux/' + arch]['manifest'] = self.save(arch + '.manifest', raw)
        def adjust(index):
            item = next(x for x in index['manifests'] if x['platform']['architecture'] == arch)
            item['digest'], item['size'] = 'sha256:' + ref.sha(raw), len(raw)
        self.replace_root(row, adjust)

    def verify_image(self, row, original=None):
        ref.verify_image(self.root, row, original or {'reference': 'fixture/image:v1'})

    def archive(self, entries=None, mutation=None):
        entries = entries or [('package.json', b'{}', 0o644), ('apps/studio/Dockerfile', b'FROM x', 0o755)]
        rows = [{'path': n, 'mode': '100755' if m & 0o111 else '100644',
                 'git_blob': ref.git_hash('blob', b)} for n, b, m in entries]
        commit = ('tree ' + ref.inventory_tree(rows) + '\nauthor fixture <a@b> 0 +0000\n\nfixture\n').encode()
        expected = ref.git_hash('commit', commit)
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:gz') as archive:
            for n, b, m in entries:
                item = tarfile.TarInfo('supabase-' + expected + '/' + n)
                item.mode, item.size = m, len(b)
                if mutation:
                    mutation(item)
                archive.addfile(item, io.BytesIO(b) if item.isfile() else None)
        return stream.getvalue(), commit, expected

    def verify_archive(self, values, limits=None):
        raw, commit, expected = values
        return ref.archive_inventory(raw, commit, limits or ref.LIMITS, expected)

    def test_complete_tree_control(self):
        rows = self.verify_archive(self.archive())
        self.assertEqual([x['path'] for x in rows], ['apps/studio/Dockerfile', 'package.json'])
        self.assertEqual(rows[0]['mode'], '100755')

    def symlink_archive(self):
        target = b'dir/file'
        blob = hashlib.sha1(b'blob 8\0' + target).digest()
        tree_data = b'120000 link\0' + blob
        tree = hashlib.sha1(b'tree ' + str(len(tree_data)).encode() + b'\0' + tree_data).hexdigest()
        commit = ('tree ' + tree + '\n\nfixture\n').encode()
        expected = hashlib.sha1(b'commit ' + str(len(commit)).encode() + b'\0' + commit).hexdigest()
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:gz') as archive:
            entry = tarfile.TarInfo('supabase-' + expected + '/link')
            entry.type, entry.linkname, entry.mode = tarfile.SYMTYPE, target.decode(), 0o777
            archive.addfile(entry)
        return stream.getvalue(), commit, expected

    def test_internal_symlink_blob_control(self):
        rows = self.verify_archive(self.symlink_archive(), dict(ref.LIMITS, file_bytes=8))
        self.assertEqual((rows[0]['mode'], rows[0]['bytes']), ('120000', 8))

    def test_internal_symlink_declared_blob_cap(self):
        with self.assertRaisesRegex(ref.Refusal, 'blob byte budget'):
            self.verify_archive(self.symlink_archive(), dict(ref.LIMITS, file_bytes=1))

    def test_commit_object_tampering(self):
        raw, commit, expected = self.archive()
        with self.assertRaisesRegex(ref.Refusal, 'commit object'):
            self.verify_archive((raw, commit + b'!', expected))

    def test_source_blob_changes_tree(self):
        raw, commit, expected = self.archive()
        other, _, _ = self.archive([('package.json', b'changed', 0o644)])
        with self.assertRaises(ref.Refusal):
            self.verify_archive((other, commit, expected))

    def test_path_traversal(self):
        with self.assertRaisesRegex(ref.Refusal, 'relative path'):
            self.verify_archive(self.archive(mutation=lambda x: setattr(x, 'name', x.name + '/../bad')))

    def test_archive_duplicate(self):
        entries = [('package.json', b'{}', 0o644), ('package.json', b'{}', 0o644)]
        with self.assertRaisesRegex(ref.Refusal, 'duplicate'):
            self.archive(entries)

    def test_archive_hardlink(self):
        with self.assertRaisesRegex(ref.Refusal, 'entry type'):
            self.verify_archive(self.archive(mutation=lambda x: setattr(x, 'type', tarfile.LNKTYPE)))

    def test_archive_device(self):
        with self.assertRaisesRegex(ref.Refusal, 'entry type'):
            self.verify_archive(self.archive(mutation=lambda x: setattr(x, 'type', tarfile.CHRTYPE)))

    def test_archive_setuid(self):
        with self.assertRaisesRegex(ref.Refusal, 'mode'):
            self.verify_archive(self.archive(mutation=lambda x: setattr(x, 'mode', 0o4755)))

    def test_source_parent_file_conflict(self):
        rows = [{'path': 'a', 'mode': '100644', 'git_blob': 'a' * 40},
                {'path': 'a/b', 'mode': '100644', 'git_blob': 'b' * 40}]
        with self.assertRaisesRegex(ref.Refusal, 'directory'):
            ref.inventory_tree(rows)

    def test_source_entry_limit(self):
        limits = dict(ref.LIMITS, entries=1)
        with self.assertRaisesRegex(ref.Refusal, 'entry budget'):
            self.verify_archive(self.archive(), limits)

    def test_source_expanded_limit(self):
        with self.assertRaisesRegex(ref.Refusal, 'expanded'):
            self.verify_archive(self.archive(), dict(ref.LIMITS, expanded_bytes=1))

    def test_gzip_trailer_tampering(self):
        raw, commit, expected=self.archive()
        with self.assertRaises((OSError, ref.Refusal)):
            self.verify_archive((raw[:-4]+b'bad!',commit,expected))

    def test_source_mode_changes_tree(self):
        values=self.archive(mutation=lambda x:setattr(x,'mode',0o644))
        with self.assertRaisesRegex(ref.Refusal,'tree identity'):
            self.verify_archive(values)

    def test_archive_root_substitution(self):
        values=self.archive(mutation=lambda x:setattr(x,'name','easier-'+x.name))
        with self.assertRaisesRegex(ref.Refusal,'root identity'):
            self.verify_archive(values)

    def test_escaping_source_symlink(self):
        def alter(x):
            x.type=tarfile.SYMTYPE;x.linkname='../../../../etc/passwd'
        with self.assertRaisesRegex(ref.Refusal,'symlink'):
            self.verify_archive(self.archive(mutation=alter))

    def source_policy(self, change):
        packet={'schema':1,'reference':ref.REFERENCE,'baseline':ref.BASELINE,'limits':ref.LIMITS,
            'archive':{},'commit_object':{},'files':[],'states':dict(ref.STATES),
            'gaps':['external Studio base/package/build inputs unproven','publisher build provenance unproven',
                    'working tree/build/runtime/security unrun']}
        change(packet);p=self.root/'source.json';p.write_text(json.dumps(packet))
        return p

    def test_source_state_escalation(self):
        p=self.source_policy(lambda d:d['states'].update(acceptance='accepted'))
        with self.assertRaisesRegex(ref.Refusal,'escalation'):
            ref.verify_source(p)

    def test_source_closure_gap_removed(self):
        p=self.source_policy(lambda d:d.update(gaps=[]))
        with self.assertRaisesRegex(ref.Refusal,'gap removed'):
            ref.verify_source(p)

    def test_source_schema_bool(self):
        p=self.source_policy(lambda d:d.update(schema=True))
        with self.assertRaisesRegex(ref.Refusal,'schema'):
            ref.verify_source(p)

    def test_source_extra_key(self):
        p=self.source_policy(lambda d:d.update(production='accepted'))
        with self.assertRaisesRegex(ref.Refusal,'extra'):
            ref.verify_source(p)

    def test_bool_budget_refused(self):
        with self.assertRaisesRegex(ref.Refusal, 'integer'):
            self.verify_archive(self.archive(), dict(ref.LIMITS, requests=True))

    def test_float_budget_refused(self):
        with self.assertRaisesRegex(ref.Refusal, 'integer'):
            self.verify_archive(self.archive(), dict(ref.LIMITS, requests=1.0))

    def test_extra_budget_refused(self):
        with self.assertRaises(ref.Refusal):
            self.verify_archive(self.archive(), dict(ref.LIMITS, extra=1))

    def test_duplicate_json_key(self):
        with self.assertRaisesRegex(ref.Refusal, 'duplicate'):
            ref.decode(b'{"state":1,"state":2}')

    def test_float_and_nonfinite_json(self):
        for raw in [b'{"a":1.0}', b'{"a":NaN}', b'{"a":Infinity}']:
            with self.subTest(raw=raw), self.assertRaises(ref.Refusal):
                ref.decode(raw)

    def test_complete_platform_config_control(self):
        self.verify_image(self.image())

    def test_manifest_layers_required(self):
        row = self.image()
        self.replace_child(row, 'amd64', lambda d: d.pop('layers'))
        with self.assertRaisesRegex(ref.Refusal, 'layers'):
            self.verify_image(row)

    def test_manifest_layers_wrong_type(self):
        for value in (True, {}, None, 'layers'):
            row = self.image()
            self.replace_child(row, 'amd64', lambda d: d.update(layers=value))
            with self.subTest(value=value), self.assertRaises(ref.Refusal):
                self.verify_image(row)

    def test_layer_descriptor_fields(self):
        valid = {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip',
                 'digest': 'sha256:' + '0' * 64, 'size': 100}
        for key, value in (('mediaType', False), ('mediaType', 'bad'), ('digest', 'bad'),
                           ('digest', 'sha256:' + 'A' * 64), ('size', True),
                           ('size', 1.5), ('size', -1)):
            row = self.image(); layer = dict(valid); layer[key] = value
            self.replace_child(row, 'amd64', lambda d: d.update(layers=[layer]))
            with self.subTest(key=key, value=value), self.assertRaises(ref.Refusal):
                self.verify_image(row)

    def test_layer_descriptor_missing_fields(self):
        for field in ('mediaType', 'digest', 'size'):
            row = self.image()
            layer = {'mediaType': 'application/octet-stream', 'digest': 'sha256:' + '0'*64,
                     'size': 0}; del layer[field]
            self.replace_child(row, 'amd64', lambda d: d.update(layers=[layer]))
            with self.subTest(field=field), self.assertRaises(ref.Refusal):
                self.verify_image(row)

    def test_additional_index_descriptors_validated(self):
        for extra in (True, {}, {'mediaType': False, 'digest': 'bad', 'size': True},
                      {'mediaType': IMAGE, 'digest': 'sha256:'+'0'*64, 'size': 1,
                       'platform': {'os': True, 'architecture': []}}):
            row = self.image()
            self.replace_root(row, lambda d: d['manifests'].append(extra))
            with self.subTest(extra=extra), self.assertRaises(ref.Refusal):
                self.verify_image(row)

    def test_valid_attachment_and_other_architecture_retained(self):
        row = self.image()
        def add(index):
            for platform in (None, {'os': 'unknown', 'architecture': 'unknown'},
                             {'os': 'linux', 'architecture': 'ppc64le'}):
                item = {'mediaType': IMAGE, 'digest': 'sha256:'+'0'*64, 'size': 100}
                if platform is not None:
                    item['platform'] = platform
                index['manifests'].append(item)
        self.replace_root(row, add)
        self.verify_image(row)

    def test_descriptor_optional_types(self):
        for key, value in (('annotations', {'a': True}), ('urls', True),
                           ('urls', ['https://host/bad path']), ('data', True),
                           ('data', '!!'), ('artifactType', False),
                           ('platform', {'os': 'linux', 'architecture': 'arm', 'variant': True}),
                           ('platform', {'os': 'linux', 'architecture': 'arm', 'features': True})):
            row = self.image()
            extra = {'mediaType': IMAGE, 'digest': 'sha256:'+'0'*64, 'size': 1, key: value}
            self.replace_root(row, lambda d: d['manifests'].append(extra))
            with self.subTest(key=key, value=value), self.assertRaises(ref.Refusal):
                self.verify_image(row)

    def test_embedded_descriptor_hash_and_size(self):
        descriptor = {'mediaType': 'application/octet-stream', 'digest': 'sha256:'+ref.sha(b'a'),
                      'size': 1, 'data': 'YQ=='}
        ref.raw_descriptor(descriptor)
        for change in ({'size': 2}, {'digest': 'sha256:'+'0'*64}):
            with self.subTest(change=change), self.assertRaises(ref.Refusal):
                ref.raw_descriptor(dict(descriptor, **change))

    def test_mixed_index_image_refused(self):
        row = self.image()
        self.replace_root(row, lambda d: d.update(layers=[]))
        with self.assertRaises(ref.Refusal):
            self.verify_image(row)

    def test_optional_subject_descriptor_refused(self):
        row = self.image()
        self.replace_child(row, 'amd64', lambda d: d.update(subject={'size': True}))
        with self.assertRaises(ref.Refusal):
            self.verify_image(row)

    def test_image_reference_substitution(self):
        row = self.image(); row['reference'] = 'easier/image:v1'
        with self.assertRaisesRegex(ref.Refusal, 'reference'):
            self.verify_image(row)

    def test_provenance_escalation(self):
        row = self.image(); row['provenance']['state'] = 'verified'
        with self.assertRaisesRegex(ref.Refusal, 'provenance'):
            self.verify_image(row)

    def test_platform_missing(self):
        row = self.image(); del row['platforms']['linux/arm64']
        with self.assertRaises(ref.Refusal):
            self.verify_image(row)

    def test_platform_ambiguous(self):
        row = self.image()
        self.replace_root(row, lambda d: d['manifests'].append(copy.deepcopy(d['manifests'][0])))
        with self.assertRaisesRegex(ref.Refusal, 'ambiguous'):
            self.verify_image(row)

    def test_index_child_digest_mismatch(self):
        row = self.image()
        self.replace_root(row, lambda d: d['manifests'][0].update(digest='sha256:' + '0' * 64))
        with self.assertRaisesRegex(ref.Refusal, 'descriptor'):
            self.verify_image(row)

    def test_descriptor_size_bool(self):
        row = self.image()
        self.replace_root(row, lambda d: d['manifests'][0].update(size=True))
        with self.assertRaisesRegex(ref.Refusal, 'integer'):
            self.verify_image(row)

    def test_descriptor_size_float(self):
        row=self.image()
        self.replace_root(row,lambda d:d['manifests'][0].update(size=1.0))
        with self.assertRaisesRegex(ref.Refusal,'float'):
            self.verify_image(row)

    def test_descriptor_media_mismatch(self):
        row = self.image()
        self.replace_root(row, lambda d: d['manifests'][0].update(mediaType=INDEX))
        with self.assertRaisesRegex(ref.Refusal, 'media'):
            self.verify_image(row)

    def test_config_hash_mismatch(self):
        row = self.image()
        self.replace_child(row, 'amd64', lambda d: d['config'].update(digest='sha256:' + '0' * 64))
        with self.assertRaisesRegex(ref.Refusal, 'config descriptor'):
            self.verify_image(row)

    def test_config_wrong_platform(self):
        row = self.image()
        config = b'{"os":"linux","architecture":"arm64"}'
        row['platforms']['linux/amd64']['config'] = self.save('amd64.config', config)
        self.replace_child(row, 'amd64', lambda d: d['config'].update(digest='sha256:' + ref.sha(config), size=len(config)))
        with self.assertRaisesRegex(ref.Refusal, 'platform mismatch'):
            self.verify_image(row)

    def test_original_digest_cannot_drift(self):
        with self.assertRaisesRegex(ref.Refusal, 'frozen image'):
            self.verify_image(self.image(), {'reference':'fixture/image:v1','digest':'sha256:'+'0'*64})

    def test_artifact_symlink(self):
        row = self.image(); (self.root/'linked').symlink_to('index'); row['root']['path']='linked'
        with self.assertRaises(OSError):
            self.verify_image(row)

    def test_artifact_hardlink(self):
        row = self.image(); os.link(self.root/'index', self.root/'linked')
        with self.assertRaisesRegex(ref.Refusal, 'hardlinked'):
            self.verify_image(row)

    def test_artifact_path_escape(self):
        row=self.image();row['root']['path']='../index'
        with self.assertRaisesRegex(ref.Refusal, 'relative path'):
            self.verify_image(row)

    def test_extra_image_keys(self):
        row=self.image();row['accepted']=True
        with self.assertRaisesRegex(ref.Refusal, 'extra'):
            self.verify_image(row)

    def test_source_requires_real_pin(self):
        raw, commit, _ = self.archive()
        with self.assertRaisesRegex(ref.Refusal, 'commit object'):
            ref.make_source(raw, commit, ref.LIMITS)

    def test_cli_refuses_missing_packet(self):
        result=subprocess.run([sys.executable,'-m','deploy.verify.reference_supplement',
            'verify-source','--packet',str(self.root/'missing')],capture_output=True,text=True)
        self.assertEqual(result.returncode,1)
        self.assertFalse(json.loads(result.stdout)['acceptance_granted'])

    def test_cli_contract_never_grants_acceptance(self):
        result=subprocess.run([sys.executable,'-m','deploy.verify.reference_supplement','contract'],
                              capture_output=True,text=True)
        self.assertEqual(result.returncode,0)
        data=json.loads(result.stdout)
        self.assertEqual(data['states']['build_inputs'],'unproven')
        self.assertFalse(data['acceptance_granted'])

    def supplement_fixture(self, change):
        # This isolates inventory policy from source collection. It is not a source proof.
        baseline_path=Path(ref.__file__).parents[2]/'docs/engineering/benchmarks/supabase-v0.8.2.bundle.json'
        original_raw=baseline_path.read_bytes();original=json.loads(original_raw)
        packet={'schema':1,'reference':ref.REFERENCE,
            'baseline':self.save('baseline.json',original_raw),'source':self.save('source.json',b'{}'),
            'images':[{'reference':x['reference']} for x in original['images']],
            'states':{'metadata':'verified','build_inputs':'unproven','provenance':'unverified',
                'build':'unrun','runtime':'unrun','security':'unproven','acceptance':'unproven','production':'unproven'}}
        change(packet)
        path=self.root/'supplement.json';path.write_text(json.dumps(packet))
        return path,{'files':original['files']}

    def test_supplement_missing_image(self):
        path,source=self.supplement_fixture(lambda d:d['images'].pop())
        with patch.object(ref,'verify_source',return_value=source),self.assertRaisesRegex(ref.Refusal,'inventory'):
            ref.verify_supplement(path)

    def test_supplement_extra_image(self):
        path,source=self.supplement_fixture(lambda d:d['images'].append({'reference':'easier/image:v1'}))
        with patch.object(ref,'verify_source',return_value=source),self.assertRaisesRegex(ref.Refusal,'inventory'):
            ref.verify_supplement(path)

    def test_supplement_duplicate_image(self):
        path,source=self.supplement_fixture(lambda d:d['images'].__setitem__(0,d['images'][1]))
        with patch.object(ref,'verify_source',return_value=source),self.assertRaisesRegex(ref.Refusal,'duplicate'):
            ref.verify_supplement(path)

    def test_supplement_state_escalation(self):
        path,source=self.supplement_fixture(lambda d:d['states'].update(acceptance='accepted'))
        with patch.object(ref,'verify_source',return_value=source),self.assertRaisesRegex(ref.Refusal,'escalation'):
            ref.verify_supplement(path)

    def test_supplement_original_source_inventory_changed(self):
        path,source=self.supplement_fixture(lambda _d:None)
        source['files']=copy.deepcopy(source['files']);source['files'][0]['mode']='100755'
        with patch.object(ref,'verify_source',return_value=source),self.assertRaisesRegex(ref.Refusal,'docker inventory'):
            ref.verify_supplement(path)

    def test_original_baseline_tampering(self):
        path,source=self.supplement_fixture(lambda _d:None)
        packet=json.loads(path.read_text());packet['baseline']=self.save('baseline.json',b'{}')
        path.write_text(json.dumps(packet))
        with self.assertRaisesRegex(ref.Refusal,'original packet'):
            ref.verify_supplement(path)

    def test_distribution_cannot_inherit_metadata(self):
        path,source=self.supplement_fixture(lambda _d:None)
        with patch.object(ref,'verify_source',return_value=source),patch.object(ref,'verify_image'),self.assertRaisesRegex(ref.Refusal,'actual build'):
            ref.verify_supplement(path,gate='distribution')

    def test_output_parent_symlink_refused(self):
        (self.root/'parent').symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(OSError):
            ref.own_directory(self.root/'parent'/'new-output')

    def test_output_existing_directory_refused(self):
        with self.assertRaises(FileExistsError):
            ref.own_directory(self.root)

    def test_collector_preserves_429(self):
        commit=self.root/'commit';commit.write_bytes(b'fixture-only')
        budget=self.root/'budget';budget.write_text(json.dumps(ref.LIMITS))
        output=self.root/'collection'
        error=urllib.error.HTTPError(ref.URL,429,'rate limited',{'Retry-After':'60'},io.BytesIO(b'429 retained'))
        with patch.object(ref,'commit_tree',return_value=ref.TREE),patch.object(ref.urllib.request,'build_opener') as opener:
            opener.return_value.open.side_effect=error
            with self.assertRaises(urllib.error.HTTPError):ref.collect_source(commit,output,budget)
            self.assertEqual(opener.return_value.open.call_count,1)
        record=json.loads((output/'refusal.json').read_bytes())
        self.assertEqual(record['status'],429);self.assertEqual(record['retry_after'],'60')
        self.assertEqual((output/'http-refusal.body').read_bytes(),b'429 retained')
        self.assertFalse((output/'source.json').exists())

    def test_redirect_refused(self):
        with self.assertRaisesRegex(ref.Refusal,'redirect'):
            ref.NoRedirect().redirect_request(None,None,None,None,None,None)

    def test_path_depth_budget(self):
        with self.assertRaisesRegex(ref.Refusal,'depth'):
            ref.relative('/'.join(['nested']*65))

    def test_path_length_budget(self):
        with self.assertRaisesRegex(ref.Refusal,'length'):
            ref.relative('a'*1025)

    def test_actual_wall_budget_interrupts_block(self):
        started=time.monotonic()
        with self.assertRaisesRegex(ref.Refusal,'wall budget'),ref.wall_budget(1):
            time.sleep(5)
        self.assertLess(time.monotonic()-started,2)

    def test_insufficient_disk_budget_before_network(self):
        commit=self.root/'commit';commit.write_bytes(b'fixture-only')
        budget=self.root/'budget';budget.write_text(json.dumps(dict(ref.LIMITS,disk_bytes=1)))
        with patch.object(ref,'commit_tree',return_value=ref.TREE),patch.object(ref.urllib.request,'build_opener') as opener:
            with self.assertRaisesRegex(ref.Refusal,'disk budget'):
                ref.collect_source(commit,self.root/'collection',budget)
            opener.assert_not_called()
        self.assertFalse((self.root/'collection').exists())


if __name__ == '__main__':
    unittest.main()
