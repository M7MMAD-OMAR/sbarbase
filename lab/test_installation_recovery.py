"""Actual original local stores and connected operator source behavior.

These checks never establish native feature/key provenance or complete recovery.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import backup
import installation_recovery as recovery

PROJECT = Path(__file__).resolve().parent.parent


class ConnectedLocalCollectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='sbarbase-collection-')
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name)
        self.root = self.parent / 'installation'
        self.state = self.root / '.lab/upstream'
        self.private = self.root / '.secrets/upstream'
        self.state.mkdir(mode=0o700, parents=True)
        self.private.mkdir(mode=0o700, parents=True)
        (self.root / 'lab').mkdir()
        self.bun([str(PROJECT / 'lab/original_schema_fixture.ts'), 'create', str(self.state)])
        shutil.move(self.state / 'managed-keys.sqlite', self.private / 'managed-keys.sqlite')
        self.expected = json.loads((self.state / 'private-fixture.json').read_text())
        self.runtime = self.expected['runtime']
        self.output = self.parent / 'collection'
        self.secret = 'private-configuration-must-not-reach-output'
        self.write(self.private / 'runtime.json', {'admin': self.secret, 'environments': {self.runtime: {'jwt': 'j' * 64}}})
        self.write(self.private / (self.runtime + '-auth.json'), {'GOTRUE_EXTERNAL_TEST_SECRET': self.secret})
        self.write(self.state / 'endpoints.json', {self.runtime: {'auth': 'http://127.0.0.1:9999'}})
        self.bun(['-e', """
import {deploy,changeSecrets} from './src/control/functions';
const [root,runtime]=process.argv.slice(1);
deploy(root+'/.lab/upstream/functions',runtime,'hello',
 {files:{'index.ts':"import {value} from '../_shared/value.ts'; Deno.serve(()=>new Response(value));"},
  shared:{'value.ts':"export const value='original-function';"},verify_jwt:true}, 1790000000000);
changeSecrets(root+'/.secrets/upstream/functions',runtime,{secrets:{GREETING:'original-private-greeting'}});
""", str(self.root), self.runtime])

    def bun(self, arguments):
        result = subprocess.run([shutil.which('bun'), *arguments], cwd=PROJECT, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, 'Original constructor/deployment operation failed; private diagnostics withheld')
        return result

    def write(self, path, value):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text(json.dumps(value)); path.chmod(0o600)

    def collect(self, **kwargs):
        return recovery.collect(self.root, self.output, **kwargs)

    def manifest(self):
        return json.loads((self.output / 'collection.json').read_text())

    def test_actual_operator_path_collects_original_stores_without_complete_success(self):
        script = """
import {main} from './lab/sbarbase';
import {spawnSync} from 'node:child_process';
const [root,output]=process.argv.slice(1);
const deps:any={root,env:{},isTTY:false,out:console.log,err:console.error,
 run:async(argv:string[])=>{const child=spawnSync(argv[0],['-c',
 'import sys; from pathlib import Path; import backup; backup.ROOT=Path(sys.argv[1]); sys.exit(backup.main(sys.argv[2:]))',
 root,...argv.slice(2)],{cwd:process.cwd(),env:{...process.env,PYTHONPATH:'lab'},encoding:'utf8'});
 process.stdout.write(child.stdout); process.stderr.write(child.stderr); return child.status??1;}};
process.exit(await main(['backup','collect-installation','--output',output],deps));
"""
        result = self.bun(['-e', script, str(self.root), str(self.output)])
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], 'local-collection-only')
        self.assertFalse(report['complete']); self.assertFalse(report['encrypted']); self.assertFalse(report['restore_admitted'])
        self.assertNotIn(self.secret.encode(), result.stdout + result.stderr)
        self.assertTrue((self.output / '.lab/upstream/control.sqlite').exists())
        self.assertTrue((self.output / '.secrets/upstream/managed-keys.sqlite').exists())

    def test_complete_catalog_and_key_rows_survive_original_constructors(self):
        self.collect()
        for original, retained in [(self.state / 'control.sqlite', self.output / '.lab/upstream/control.sqlite'),
                                   (self.private / 'managed-keys.sqlite', self.output / '.secrets/upstream/managed-keys.sqlite')]:
            def rows(path):
                with contextlib.closing(sqlite3.connect(path)) as db:
                    tables = db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
                    return {name: db.execute('SELECT * FROM "' + name.replace('"', '""') + '"').fetchall() for name, in tables}
            self.assertEqual(rows(original), rows(retained))
        verify = self.parent / 'original-verify'; verify.mkdir(mode=0o700)
        shutil.copy(self.output / '.lab/upstream/control.sqlite', verify / 'control.sqlite')
        shutil.copy(self.output / '.secrets/upstream/managed-keys.sqlite', verify / 'managed-keys.sqlite')
        shutil.copy(self.state / 'private-fixture.json', verify / 'private-fixture.json')
        self.bun([str(PROJECT / 'lab/original_schema_fixture.ts'), 'verify', str(verify)])

    def test_private_configuration_is_retained_with_private_permissions(self):
        self.collect()
        value = self.output / '.secrets/upstream/runtime.json'
        self.assertEqual(json.loads(value.read_text())['admin'], self.secret)
        self.assertEqual(value.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o700)
        for directory in self.output.rglob('*'):
            if directory.is_dir(): self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        self.assertIn('.secrets/upstream/' + self.runtime + '-auth.json', [row['name'] for row in self.manifest()['materials']])

    def test_versioned_functions_shared_imports_and_secrets_are_exact(self):
        self.collect()
        root = '.lab/upstream/functions/' + self.runtime
        manifest = json.loads((self.output / root / 'functions.json').read_text())
        version = manifest['functions']['hello']['version']
        self.assertEqual((self.output / root / version / '_shared/value.ts').read_bytes(),
                         (self.state / 'functions' / self.runtime / version / '_shared/value.ts').read_bytes())
        self.assertEqual(json.loads((self.output / '.secrets/upstream/functions' / self.runtime / 'secrets.json').read_text()),
                         {'GREETING': 'original-private-greeting'})
        self.assertTrue(manifest['functions']['hello']['verify_jwt'])

    def test_missing_deployed_function_entrypoint_refuses_publication(self):
        next((self.state / 'functions').rglob('index.ts')).unlink()
        with self.assertRaises(recovery.Refused): self.collect()
        self.assertFalse(self.output.exists())

    def test_missing_functions_secrets_refuses_publication(self):
        (self.private / 'functions' / self.runtime / 'secrets.json').unlink()
        with self.assertRaises(recovery.Refused): self.collect()
        self.assertFalse(self.output.exists())

    def test_missing_managed_key_store_refuses_publication(self):
        (self.private / 'managed-keys.sqlite').unlink()
        with self.assertRaises(recovery.Refused): self.collect()
        self.assertFalse(self.output.exists())

    def test_symlink_private_material_refuses_without_reading_destination(self):
        original = self.private / 'runtime.json'
        original.rename(self.parent / 'hidden.json'); original.symlink_to(self.parent / 'hidden.json')
        with self.assertRaises(Exception): self.collect()
        self.assertFalse(self.output.exists())

    def test_concurrent_configuration_change_refuses_publication(self):
        original = recovery.functions
        def change(*args):
            original(*args)
            self.write(self.private / 'runtime.json', {'changed': True})
        with patch.object(recovery, 'functions', side_effect=change):
            with self.assertRaises(recovery.Refused): self.collect()
        self.assertFalse(self.output.exists())

    def test_existing_collection_is_not_overwritten(self):
        self.output.mkdir(mode=0o700); marker = self.output / 'keep'; marker.write_text('original')
        with self.assertRaises(recovery.Refused): self.collect()
        self.assertEqual(marker.read_text(), 'original')

    def test_byte_limit_refuses_without_incomplete_publication(self):
        with self.assertRaises(recovery.Refused): self.collect(max_bytes=128)
        self.assertFalse(self.output.exists())

    def test_complete_capture_command_refuses_before_source_or_native_effects(self):
        with patch.object(backup, 'ROOT', self.root), patch.object(backup, 'preflight') as native, contextlib.redirect_stderr(io.StringIO()) as diagnostic:
            self.assertEqual(backup.main(['create-installation', '--output', str(self.output), '--key-file', str(self.parent / 'absent-key')]), 1)
            native.assert_not_called()
        self.assertFalse(self.output.exists()); self.assertNotIn(self.secret, diagnostic.getvalue())

    def test_new_file_during_collection_refuses_unknown_retained_state(self):
        original = recovery.functions
        def change(*args):
            original(*args); self.write(self.private / 'new-private.json', {'new': 'state'})
        with patch.object(recovery, 'functions', side_effect=change):
            with self.assertRaises(recovery.Refused): self.collect()
        self.assertFalse(self.output.exists())

    def test_world_readable_private_material_refuses_publication(self):
        (self.private / 'runtime.json').chmod(0o644)
        with self.assertRaises(recovery.Refused): self.collect()
        self.assertFalse(self.output.exists())

    def test_output_inside_source_is_refused_without_state_changes(self):
        self.output = self.root / 'local-collection'
        with self.assertRaises(recovery.Refused): self.collect()
        self.assertFalse(self.output.exists())

    def test_collection_manifest_digests_bind_every_exact_member(self):
        import hashlib
        self.collect()
        for item in self.manifest()['materials']:
            payload = (self.output / item['name']).read_bytes()
            self.assertEqual(item['bytes'], len(payload))
            self.assertEqual(item['sha256'], hashlib.sha256(payload).hexdigest())
        self.assertIn('complete-authenticated-package', self.manifest()['missing_suppliers'])

    def test_failed_atomic_publication_removes_only_owned_staging(self):
        marker = self.parent / 'keep'; marker.write_text('retained')
        with patch.object(recovery, 'publish_exact', side_effect=OSError('publication refused')):
            with self.assertRaises(OSError): self.collect()
        self.assertFalse(self.output.exists())
        self.assertEqual(marker.read_text(), 'retained')
        self.assertEqual(list(self.parent.glob('.installation-collection-*')), [])

    def test_unreadable_private_subtree_refuses_instead_of_omitting_credentials(self):
        directory = self.private / 'retained-provider'
        self.write(directory / 'credential.json', {'private': self.secret})
        directory.chmod(0)
        try:
            with self.assertRaises(Exception): self.collect()
            self.assertFalse(self.output.exists())
        finally:
            directory.chmod(0o700)

    def test_original_all_five_function_entrypoints_are_collected(self):
        for suffix in ['ts', 'js', 'tsx', 'jsx', 'mjs']:
            self.bun(['-e', """
import {deployment,deploy} from './src/control/functions';
const [root,runtime,suffix]=process.argv.slice(1);
const input=deployment({files:{['index.'+suffix]:"Deno.serve(()=>new Response('supported-original-entrypoint'));"}});
deploy(root+'/.lab/upstream/functions',runtime,'entry-'+suffix,input);
""", str(self.root), self.runtime, suffix])
        self.collect()
        manifest=json.loads((self.output / '.lab/upstream/functions' / self.runtime / 'functions.json').read_text())
        for suffix in ['ts', 'js', 'tsx', 'jsx', 'mjs']:
            entry=manifest['functions']['entry-'+suffix]
            self.assertTrue((self.output / '.lab/upstream/functions' / self.runtime / entry['version'] / ('entry-'+suffix) / ('index.'+suffix)).exists())

    def test_missing_known_shared_import_refuses_publication(self):
        next((self.state / 'functions').rglob('value.ts')).unlink()
        with self.assertRaises(recovery.Refused): self.collect()
        self.assertFalse(self.output.exists())

    def test_ambiguous_dynamic_import_refuses_local_closure(self):
        file = next((self.state / 'functions').rglob('index.ts'))
        file.write_text("const path = Deno.env.get('MODULE'); await import(path); Deno.serve(()=>new Response('x')); ")
        with self.assertRaises(recovery.Refused): self.collect()
        self.assertFalse(self.output.exists())

    def test_nested_static_local_module_missing_refuses_closure(self):
        shared = next((self.state / 'functions').rglob('value.ts'))
        shared.write_text("export {value} from './retained-missing.ts';")
        with self.assertRaises(recovery.Refused): self.collect()
        self.assertFalse(self.output.exists())

    def test_empty_private_directory_added_during_capture_refuses(self):
        original = recovery.functions
        def change(*args):
            result = original(*args); (self.private / 'new-empty').mkdir(); return result
        with patch.object(recovery, 'functions', side_effect=change):
            with self.assertRaises(recovery.Refused): self.collect()
        self.assertFalse(self.output.exists())

    def test_missing_node_preflights_before_source_collection(self):
        with patch.object(recovery.shutil, 'which', return_value=None), patch.object(recovery, 'sources') as source:
            with self.assertRaises(recovery.Refused): self.collect()
            source.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_parser_failure_child_pipe_is_bounded_and_group_cleaned(self):
        import sys,time
        pid_file = self.parent / 'owned-parser-child.pid'
        code = "import subprocess,sys; child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); open(sys.argv[1],'w').write(str(child.pid))"
        began=time.monotonic()
        try:
            with patch.object(recovery, 'PARSER_COMMAND', [sys.executable, '-c', code, str(pid_file)]):
                with self.assertRaises(recovery.Refused): recovery.parser({}, time.monotonic()+0.3)
            self.assertLess(time.monotonic()-began, 2)
            pid=int(pid_file.read_text())
            for _ in range(20):
                try:
                    state=Path('/proc') / str(pid) / 'stat'
                    if not state.exists() or state.read_text().split()[2]=='Z': break
                # A process that exits while /proc is read answers ESRCH: it is gone, as wanted.
                except (FileNotFoundError, ProcessLookupError): break
                time.sleep(0.01)
            else: self.fail('Owned parser descendant was left running')
        finally:
            if pid_file.exists():
                try: os.kill(int(pid_file.read_text()), 9)
                except ProcessLookupError: pass
