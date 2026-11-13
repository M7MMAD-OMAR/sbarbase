"""Database-local prerequisites must not broaden Studio's authority."""
import unittest
import durable_runtime
import studio
import studio_extensions

E = 'e_' + 'a' * 24


class StudioExtensionTests(unittest.TestCase):
    def test_pg_catalog_checks_and_environment_owner_are_explicit(self):
        sql = studio_extensions.prepare_sql(E)
        self.assertIn('pg_catalog.pg_available_extensions', sql)
        self.assertIn("name = 'pgmq'", sql)
        self.assertIn('CREATE SCHEMA IF NOT EXISTS pgmq AUTHORIZATION ' + E + '_developer', sql)
        self.assertNotIn('CREATE EXTENSION', sql)
        self.assertNotIn('SUPERUSER', sql)
        self.assertNotIn('TO PUBLIC', sql)

    def test_fresh_developer_and_reopened_studio_both_prepare_the_prerequisite(self):
        prerequisite = studio_extensions.prepare_sql(E)
        self.assertIn(prerequisite, durable_runtime.Runtime.developer_grants_sql(None, E))
        text = studio.role_sql(E, 'f' * 64)
        self.assertIn(prerequisite, text)
        self.assertLess(text.index(prerequisite), text.index('ALTER ROLE ' + E + '_studio LOGIN'))

    def test_identifier_injection_is_refused(self):
        for value in ('postgres', E + '; DROP DATABASE postgres', E.upper(), ''):
            with self.assertRaises(ValueError):
                studio_extensions.prepare_sql(value)

    def test_installer_verifies_exact_identity_permissions_and_bytes(self):
        import json
        from types import SimpleNamespace
        import hashlib
        calls = []
        cid, image, owner = 'a' * 64, 'sha256:' + 'b' * 64, 'fixture'
        content = studio_extensions.SOURCE.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        def docker(*args, **kwargs):
            calls.append((args, kwargs))
            if args[0] == 'inspect':
                return SimpleNamespace(stdout=json.dumps([{'Id':cid,'Image':image,'Config':{'Labels':{'io.sbarbase.owner':owner}}}]))
            return SimpleNamespace(stdout='' if '-i' in args else '0:0:444\n'+digest+'  '+studio_extensions.PGMQ_SCRIPT+'\n')
        self.assertEqual(studio_extensions.install(docker,cid,image,owner),digest)
        self.assertEqual([item[0][0] for item in calls],['inspect','exec','exec','inspect'])
        self.assertIn('mv -f',calls[1][0][7])
        self.assertEqual(calls[1][1]['data'].encode(),content)
        with self.assertRaisesRegex(RuntimeError,'permissions'):
            def writable(*args,**kwargs):
                value=docker(*args,**kwargs)
                value.stdout=value.stdout.replace('0:0:444','0:0:644')
                return value
            studio_extensions.install(writable,cid,image,owner)
        with self.assertRaisesRegex(RuntimeError,'container identity'):
            studio_extensions.install(docker,'not-a-container',image,owner)

    def test_existing_hosted_ownership_is_repaired_without_dropping_the_extension(self):
        text=studio_extensions.prepare_sql(E)
        self.assertIn("r.rolname = 'postgres'",text)
        self.assertIn("e.extversion = '1.5.1'",text)
        self.assertIn('extowner = owner_role::regrole',text)
        self.assertNotIn('DROP EXTENSION',text)

    def test_shared_server_statistics_stay_blocked(self):
        self.assertIn('pg_stat_statements', durable_runtime.RESERVED_EXTENSIONS)
        self.assertIn('pg_stat_monitor', durable_runtime.RESERVED_EXTENSIONS)


"""Disposable retained database start proof. Requires the pinned image and Docker."""
import hashlib
import json
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import durable_runtime as runtime
import hba_generation
import hba_runtime
import hba_startup
import hba_target
import studio_extensions

OWNER = 'retained-studio-extension-proof'
IMAGE = json.loads((runtime.lab.ROOT / 'lab/distro-image.lock.json').read_text())['id']


def docker(*args, data=None, check=True, timeout=120):
    return subprocess.run(['docker', *args], input=data, text=True, capture_output=True, check=check, timeout=timeout)


def retained_runtime_check():
    name = 'sbar-retained-proof-' + uuid.uuid4().hex
    password = uuid.uuid4().hex
    checks = []
    cid = None
    with tempfile.TemporaryDirectory(prefix='retained-studio-proof-') as directory:
        state = Path(directory)
        cidfile = state / 'cid'
        try:
            cid = docker('run', '-d', '--pull=never', '--restart=no', '--cidfile', str(cidfile),
                         '--name', name, '--label', 'io.sbarbase.owner=' + OWNER,
                         '--network', 'none', '--memory', '1024m', '--cpus', '1', '--pids-limit', '512',
                         '-e', 'POSTGRES_PASSWORD=' + password, '-e', 'POSTGRES_HOST=/var/run/postgresql',
                         '-e', 'POSTGRES_DB=postgres', IMAGE, 'postgres', '-c',
                         'config_file=/etc/postgresql/postgresql.conf', '-c', 'log_statement=none').stdout.strip()
            assert re.fullmatch('[a-f0-9]{64}', cid)
            deadline = time.monotonic() + 120
            while True:
                probe = docker('exec', cid, 'psql', '-X', '-qAt', '-U', 'supabase_admin', '-d', 'postgres',
                               '-c', "SELECT to_regrole('supabase_privileged_role') IS NOT NULL", check=False)
                if probe.returncode == 0 and probe.stdout.strip() == 't':
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError('Database readiness timed out: ' + probe.stderr)
                time.sleep(1)
            target = hba_target.capture(docker, name, OWNER, IMAGE)
            with hba_startup.acquire(state) as lease:
                lease.initialize(docker, target=target)
            original = hba_generation.read_existing(docker, state, target=target)
            pin = (state / hba_generation.NAME).read_bytes()
            # Corrupt the root-owned hook before startup. Reconciliation must replace it.
            docker('exec', '--user', '0', cid, 'sh', '-c', 'printf corrupted > "$1"; chmod 0666 "$1"',
                   'proof', studio_extensions.PGMQ_SCRIPT)
            class DatabasePhaseComplete(Exception):
                pass
            def stop_after_database(*args, **kwargs):
                raise DatabasePhaseComplete()
            instance = runtime.Runtime.__new__(runtime.Runtime)
            instance.values = {'admin': password, 'environments': {}}
            instance.pins = {'db': json.loads((runtime.lab.ROOT / 'lab/distro-image.lock.json').read_text())}
            instance.kept = None
            instance.touched = set()
            actual_launch = runtime.Runtime.launch
            actual_inspect = runtime.inspect
            def inspect_isolated(kind, resource):
                if kind == 'network' and resource == 'none':
                    return {'Name': 'none'}
                return actual_inspect(kind, resource)
            def launch_on_isolated_network(self, *args, **kwargs):
                kwargs['network'] = 'none'
                return actual_launch(self, *args, **kwargs)
            with hba_startup.acquire(state) as lease, \
                 patch.object(runtime, 'STATE', state), patch.object(runtime, 'DB', name), \
                 patch.object(runtime, 'OWNER', OWNER), patch.object(runtime, 'NETWORK', 'none'), \
                 patch.object(runtime, 'inspect', side_effect=inspect_isolated), \
                 patch.object(runtime, 'database_mounts', return_value=()), \
                 patch.object(runtime, 'publish_internal_route'), \
                 patch.object(runtime, 'realtime_count', return_value=0), \
                 patch.object(runtime, 'functions_count', return_value=0), \
                 patch.object(runtime.kept_runtime, 'current', return_value=(None, '')), \
                 patch.object(runtime.kept_runtime, 'admission_credit_mib', return_value=0), \
                 patch.object(runtime.Runtime, 'launch', launch_on_isolated_network), \
                 patch.object(runtime.Runtime, 'endpoint', return_value='http://127.0.0.1:5432'), \
                 patch.object(runtime.runtime_files, 'write_database', side_effect=stop_after_database):
                writer = hba_runtime.SourceHBA(docker, state, name, OWNER, IMAGE, startup=lease)
                instance.hba_writer = writer
                writer.before_start(runtime.inspect('container', name), False)
                try:
                    instance.start()
                except DatabasePhaseComplete:
                    pass
                else:
                    raise AssertionError('Expected stop after database phase')
                assert writer.target == target and writer.fresh is False
            info = json.loads(docker('inspect', cid).stdout)[0]
            assert info['Id'] == cid and info['Name'] == '/' + name and info['Image'] == IMAGE
            assert hba_generation.read_existing(docker, state, target=target) == original
            assert (state / hba_generation.NAME).read_bytes() == pin
            checks.append('Runtime.start retained exact container and native authority and pin')
            actual = docker('exec', '--user', '0', cid, 'sh', '-c', 'stat -c "%u:%g:%a" "$1"; sha256sum "$1"',
                            'proof', studio_extensions.PGMQ_SCRIPT).stdout.splitlines()
            digest = hashlib.sha256(studio_extensions.SOURCE.read_bytes()).hexdigest()
            assert actual == ['0:0:444', digest + '  ' + studio_extensions.PGMQ_SCRIPT]
            checks.append('Runtime.start repaired corrupted hook bytes and permissions')
            deny = docker('exec', '--user', 'postgres', cid, 'sh', '-c', 'printf changed > "$1"',
                          'proof', studio_extensions.PGMQ_SCRIPT, check=False)
            assert deny.returncode != 0
            checks.append('PostgreSQL OS account cannot overwrite hook')
            sql = "CREATE ROLE retained_proof_tenant LOGIN; SET ROLE retained_proof_tenant; COPY (SELECT 'changed') TO PROGRAM 'cat > " + studio_extensions.PGMQ_SCRIPT + "';"
            deny_sql = docker('exec', '-i', cid, 'psql', '-X', '-v', 'ON_ERROR_STOP=1', '-U', 'supabase_admin',
                              '-d', 'postgres', data=sql, check=False)
            assert deny_sql.returncode != 0 and 'permission denied' in deny_sql.stderr.lower(), deny_sql.stderr
            assert docker('exec', cid, 'sha256sum', studio_extensions.PGMQ_SCRIPT).stdout.split()[0] == digest
            checks.append('Unprivileged SQL role cannot write hook and bytes remain unchanged')
        finally:
            if cid is None and cidfile.is_file():
                cid = cidfile.read_text().strip()
            if cid:
                assert re.fullmatch('[a-f0-9]{64}', cid)
                info = json.loads(docker('inspect', cid).stdout)[0]
                assert info['Id'] == cid and info['Name'] == '/' + name and info['Image'] == IMAGE
                assert info['Config']['Labels'].get('io.sbarbase.owner') == OWNER
                docker('rm', '-fv', cid)
                assert cid not in docker('ps', '-aq', '--no-trunc').stdout.split()
                checks.append('Exact disposable container and its anonymous volumes removed')
    print(json.dumps({'image': IMAGE, 'checks': checks}, indent=2))


class RetainedRuntimeTests(unittest.TestCase):
    def test_retained_database_hook_and_authority(self):
        retained_runtime_check()


if __name__ == '__main__':
    unittest.main()
