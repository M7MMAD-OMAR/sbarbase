"""Rebuild the installation's database cluster from one backup run (upstream currency P3, part 3).

    /usr/bin/python3 lab/cluster_rebuild.py <run> [--image-at COMMIT]

<run> is the time of a backup run of every environment (``lab/backup.py create all``): its
environment databases, ``storage_metadata``, the management database and its installation
manifest, whose cluster inventory (lab/cluster_inventory.py) says which roles the runtime
manages. The rebuild starts the chosen PostgreSQL image (this checkout's distribution lock, or the
lock of COMMIT, proven on this daemon and never pulled) in a build container on a new volume:

  1. the volume ``sbarbase-durable-pgdata-<12 hex>``, owned by the runtime (it mounts no other);
  2. the build container ``sbarbase-rebuild-<12 hex>``, owner ``durable-rebuild`` (nothing that
     settles or counts the runtime's containers acts on it), without network;
  3. the managed roles from the inventory, with the passwords the runtime itself keeps
     (``.secrets/upstream/runtime.json``, and the developer login's own file); a login whose
     password is not kept (Studio's, one per session; a developer login without its file) is
     created closed, and the runtime opens it again when it is turned on;
  4. the management database, ``storage_metadata`` and every environment database, each through
     the same archive planning a restore uses (lab/restore_sql.py), checked against the counts
     its backup recorded, then renamed to its own name;
  5. the inventory taken again on the rebuilt cluster, which must match the recorded one (login
     state aside for the logins that open and close);
  6. the build container is stopped and removed; the volume stays.

Nothing of the installation changes: Sbarbase must be stopped (the supervisor and worker locks
are held throughout), and the rebuilt volume is taken into use only by a generation migration
(``lab/migrate-generation.py --to-volume``, part 4). A rebuild that fails removes its build
container and keeps its volume, which nothing uses; it is reported, never removed here. The
record of the last rebuild is ``.lab/upstream/cluster-rebuild.json``.
"""
import argparse
import contextlib
import io
import json
import re
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import backup
import backup_offsite
import cluster_inventory
import database_volume
import durable_runtime as runtime
import release_channel
import resource_policy
import run as lab
import upgrade
from restore_sql import PlanError, plan_archive

OWNER = 'durable-rebuild'
RECORD = runtime.STATE / 'cluster-rebuild.json'
# The logins whose login state opens and closes; the rebuilt cluster may differ there.
TOGGLED = {'studio', 'developer', 'realtime'}
DATA = '/var/lib/postgresql/data'
COMMAND = ('postgres', '-c', 'config_file=/etc/postgresql/postgresql.conf', '-c', 'log_statement=none')


class RebuildError(RuntimeError):
    pass


def docker(*args, data=None, check=True):
    return lab.docker(*args, data=data, check=check)


def identifier(name):
    return '"' + name.replace('"', '""') + '"'


# ---------------------------------------------------------------- inputs

def load_run(stamp):
    """The verified backups of one run: {scope: (database, archive path, manifest)} in restore
    order (management, storage_metadata, then each environment) and the cluster inventory."""
    if not backup.STAMP.fullmatch(str(stamp)):
        raise RebuildError('Not a backup run time')
    installation = backup.BACKUPS / backup_offsite.INSTALLATION / stamp
    try:
        backup_offsite.verify_installation(installation)
        manifest = json.loads((installation / 'installation.json').read_text())
    except (OSError, ValueError, backup.BackupError) as error:
        raise RebuildError(f'The run has no verified installation manifest ({error})') from None
    inventory = manifest.get('cluster')
    problems = cluster_inventory.usable(inventory)
    if problems:
        raise RebuildError('The run cannot be rebuilt from: ' + '; '.join(problems))
    environments = manifest.get('environments')
    if not isinstance(environments, list) or not all(isinstance(e, str) and re.fullmatch(r'e_[a-f0-9]{24}', e)
                                                      for e in environments):
        raise RebuildError('The run lists no environments')
    scopes = [backup.MANAGEMENT, backup.STORAGE, *sorted(environments)]
    found = {}
    for scope in scopes:
        path = backup.BACKUPS / scope / stamp
        try:
            found[scope] = (backup.scope_database(scope), path, backup.verify_scope(scope, path))
        except (OSError, ValueError, backup.BackupError) as error:
            raise RebuildError(f'The run has no complete backup of {scope} ({error})') from None
    recorded = {role['environment'] for role in inventory['roles'] if role['environment']}
    if recorded - set(environments):
        raise RebuildError('The inventory names environments the run did not back up: ' + ', '.join(sorted(recorded - set(environments))))
    return found, inventory


def chosen_image(commit=None):
    """(reference, native id) of the PostgreSQL image, proven on this daemon."""
    if commit is None:
        pin = backup.image_pin('distro-image.lock.json')
    else:
        pin = release_channel.lock_at(upgrade.resolve(commit), release_channel.DATABASE_LOCK)
        if not pin:
            raise RebuildError('That version has no PostgreSQL lock')
    try:
        return backup.resolve_image(pin)
    except backup.BackupError as error:
        raise RebuildError(f'The PostgreSQL image is not proved on this host ({error}); pull it first') from None


def passwords():
    """The passwords the runtime keeps, by role name. Read here, never printed or recorded."""
    try:
        values = json.loads((runtime.PRIVATE / 'runtime.json').read_text())
    except (OSError, ValueError):
        raise RebuildError('The runtime credentials cannot be read; run this as the service account') from None
    found = {}
    if isinstance(values.get('storage_control'), str):
        found['storage_control'] = values['storage_control']
    management = values.get('management')
    if isinstance(management, dict) and isinstance(management.get('auth'), str):
        found['management_auth'] = management['auth']
    for e, item in (values.get('environments') or {}).items():
        if not isinstance(item, dict):
            continue
        for kind in ('auth', 'rest', 'storage', 'realtime'):
            if isinstance(item.get(kind), str):
                found[f'{e}_{kind}'] = item[kind]
        developer = runtime.PRIVATE / 'database' / f'{e}.json'
        try:
            password = json.loads(developer.read_text()).get('password')
        except (OSError, ValueError, AttributeError):
            password = None
        if isinstance(password, str):
            found[f'{e}_developer'] = password
    return found


# ---------------------------------------------------------------- SQL

# A list setting is stored as its SET text ("public, extensions"); quoting it whole would make
# one name of it. Its elements are written back as they were stored, after this check.
LIST_SETTINGS = {'search_path'}
LIST_VALUE = re.compile(r'(?:"[^"]*"|[A-Za-z_$][A-Za-z0-9_$]*)(?:, (?:"[^"]*"|[A-Za-z_$][A-Za-z0-9_$]*))*')


def setting_sql(setting):
    key, separator, value = setting.partition('=')
    if not separator or not re.fullmatch(r'[a-z_][a-z0-9_.]*', key):
        raise RebuildError('A recorded role setting is malformed')
    if key in LIST_SETTINGS:
        if not LIST_VALUE.fullmatch(value):
            raise RebuildError(f'The recorded {key} cannot be written back')
        return f'{identifier(key)} TO {value}'
    return f'{identifier(key)} TO {runtime.sql_literal(value)}'


# The CREATE ROLE keyword of each pg_roles attribute.
KEYWORDS = {'rolsuper': 'SUPERUSER', 'rolinherit': 'INHERIT', 'rolcreaterole': 'CREATEROLE',
            'rolcreatedb': 'CREATEDB', 'rolreplication': 'REPLICATION', 'rolbypassrls': 'BYPASSRLS'}


def role_sql(inventory, known):
    """(statements, closed): the SQL that creates every managed role as the inventory records it,
    and the logins created closed because no password is kept for them. A login the runtime
    must always open (Auth, REST, Storage, the cluster logins) without a password refuses."""
    statements, grants, closed = [], [], []
    for role in inventory['roles']:
        name = role['name']
        attributes = ' '.join(('' if role[key] else 'NO') + KEYWORDS[key] for key in cluster_inventory.ATTRIBUTES)
        login = role['rolcanlogin']
        password = known.get(name)
        if login and password is None:
            if role['kind'] not in ('studio', 'developer'):
                raise RebuildError(f'No password is kept for {name}; it cannot be rebuilt')
            closed.append(name)
            login = False
        statements.append(f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {runtime.sql_literal(name)}) "
                          f"THEN CREATE ROLE {identifier(name)}; END IF; END $$;")
        statements.append(f"ALTER ROLE {identifier(name)} WITH {attributes} {'LOGIN' if login else 'NOLOGIN'} "
                          f"CONNECTION LIMIT {int(role['rolconnlimit'])}"
                          + (f" PASSWORD {runtime.sql_literal(password)}" if password is not None else '') + ';')
        for setting in role['settings']:
            statements.append(f'ALTER ROLE {identifier(name)} SET {setting_sql(setting)};')
        for grant in role['member_of']:
            grants.append(f"GRANT {identifier(grant['role'])} TO {identifier(name)} WITH "
                          f"INHERIT {'TRUE' if grant['inherit'] else 'FALSE'}, SET {'TRUE' if grant['set'] else 'FALSE'}, "
                          f"ADMIN {'TRUE' if grant['admin'] else 'FALSE'};")
    parameters = [f"GRANT {item['privilege']} ON PARAMETER {identifier(item['parameter'])} TO {identifier(item['grantee'])};"
                  for item in inventory['parameters']]
    return statements + grants + parameters, closed


def comparable(inventory, closed):
    """The inventory with login state left out where the rebuilt cluster may differ."""
    roles = []
    for role in inventory['roles']:
        role = dict(role)
        if role['kind'] in TOGGLED or role['name'] in closed:
            role['rolcanlogin'] = None
        roles.append(role)
    return {'roles': sorted(roles, key=lambda role: role['name']),
            'parameters': sorted(inventory['parameters'], key=lambda item: json.dumps(item, sort_keys=True))}


# ---------------------------------------------------------------- the build container

class Build:
    def __init__(self, container):
        self.container = container

    def psql(self, database='postgres'):
        return ['docker', 'exec', '-i', self.container, 'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1',
                '-U', 'supabase_admin', '-d', database]

    def execute(self, query, database='postgres'):
        result = subprocess.run(self.psql(database), input=query, capture_output=True, text=True, timeout=3600)
        if result.returncode:
            raise RebuildError(f'A statement failed in {database} on the rebuilt cluster')
        return result.stdout.strip()

    def ready(self, deadline=120):
        end = time.monotonic() + deadline
        while time.monotonic() < end:
            probe = docker('exec', self.container, 'pg_isready', '-h', '127.0.0.1', check=False)
            if probe.returncode == 0:
                try:
                    if self.execute("SELECT to_regrole('supabase_privileged_role') IS NOT NULL;") == 't':
                        return True
                except RebuildError:
                    pass
            time.sleep(1)
        raise RebuildError('The rebuilt cluster did not become ready')


@contextlib.contextmanager
def planned(build, archive, database, stage):
    """The archive planned as a restore plans it: (plan, replay file)."""
    with tempfile.TemporaryFile() as generated, tempfile.TemporaryFile() as replay:
        with (archive / 'database.dump').open('rb') as source:
            result = backup.run(['docker', 'exec', '-i', build.container, 'pg_restore', '--create', '--exit-on-error',
                                 '--file=-'], stdin=source, stdout=generated, text=False, check=False)
        if result.returncode or result.stderr:
            raise RebuildError(f'The archive of {database} could not be planned')
        generated.seek(0)
        with io.TextIOWrapper(generated, encoding='utf-8') as source, \
                io.TextIOWrapper(replay, encoding='utf-8', write_through=True) as output:
            plan = plan_archive(source, output, database, stage)
            output.flush()
            replay.seek(0)
            yield plan, replay


def restore_database(build, scope, database, archive, manifest):
    """One database into the rebuilt cluster, under a stage name, checked, then renamed."""
    stage = database + '_rebuild'
    with planned(build, archive, database, stage) as (plan, replay):
        if plan.roles:
            names = ','.join(runtime.sql_literal(role) for role in plan.roles)
            observed = json.loads(build.execute(f"SELECT coalesce(json_agg(rolname ORDER BY rolname),'[]'::json) "
                                                f"FROM pg_roles WHERE rolname IN ({names});"))
            if observed != list(plan.roles):
                raise RebuildError(f'A role the archive of {database} names is missing on the rebuilt cluster')
        build.execute(plan.create)
        result = subprocess.run(build.psql(stage), stdin=replay, capture_output=True, timeout=3600)
        if result.returncode:
            raise RebuildError(f'The archive of {database} did not replay')
    if plan.metadata:
        build.execute('\n'.join(plan.metadata), stage)
    actual = backup.restore_counts(scope, lambda query: build.execute(query, stage))
    if actual != backup.expected_counts(scope, manifest):
        raise RebuildError(f'The rebuilt {database} does not hold the rows its backup recorded')
    build.execute(f'ALTER DATABASE {identifier(stage)} RENAME TO {identifier(database)};')


# ---------------------------------------------------------------- the rebuild

HEADROOM_MIB = 1536


def available_mib():
    for line in Path('/proc/meminfo').read_text().splitlines():
        if line.startswith('MemAvailable:'):
            return int(line.split()[1]) // 1024
    return 0


def save(record):
    lab.atomic(RECORD, record)


def held_locks():
    """Sbarbase stopped for the whole rebuild: its supervisor and worker locks, and the backup lock."""
    stack = contextlib.ExitStack()
    for path, refusal in ((upgrade.SUPERVISOR_LOCK, 'Sbarbase is running; stop it first'),
                          (runtime.STATE / 'worker.lock', 'The provisioning worker is running; stop Sbarbase first'),
                          (upgrade.BACKUP_LOCK, 'A backup or restore is running')):
        try:
            stack.enter_context(upgrade.exclusive(path, refusal))
        except upgrade.UpgradeError as error:
            stack.close()
            raise RebuildError(str(error)) from None
    return stack


def rebuild(stamp, commit=None, token=None):
    with held_locks():
        scopes, inventory = load_run(stamp)
        reference, image = chosen_image(commit)
        known = passwords()
        statements, closed = role_sql(inventory, known)
        token = token or secrets.token_hex(6)
        volume = f'sbarbase-durable-pgdata-{token}'
        container = f'sbarbase-rebuild-{token}'
        if not database_volume.rebuilt(volume):
            raise RebuildError('Not a rebuilt volume name')
        if available_mib() < HEADROOM_MIB:
            raise RebuildError(f'The host has less than {HEADROOM_MIB} MiB of memory to spare for the build container')
        for kind, name in (('volume', volume), ('container', container)):
            if docker(kind, 'inspect', name, check=False).returncode == 0:
                raise RebuildError(f'{name} already exists')
        record = {'version': 1, 'run': stamp, 'image': image, 'volume': volume, 'container': container,
                  'phase': 'started', 'started_at': upgrade.now(), 'closed_logins': closed}
        save(record)
        build = Build(container)
        try:
            docker('volume', 'create', '--label', 'io.sbarbase.owner=' + runtime.OWNER, volume)
            docker('run', '-d', '--pull', 'never', '--name', container, '--label', 'io.sbarbase.owner=' + OWNER,
                   *resource_policy.labels('maintenance'), '--network', 'none', '--memory', '1024m',
                   '--memory-swap', '1024m', '--cpus', '1', '--pids-limit', '256',
                   '--log-opt', 'max-size=5m', '--log-opt', 'max-file=2',
                   '--env-file', str(runtime.PRIVATE / (runtime.DB + '.env')),
                   '-v', volume + ':' + DATA, image, *COMMAND)
            build.ready()
            build.execute('\n'.join(statements))
            for scope, (database, archive, manifest) in scopes.items():
                restore_database(build, scope, database, archive, manifest)
                record = {**record, 'restored': [*record.get('restored', []), scope]}
                save(record)
            rebuilt = cluster_inventory.from_rows(json.loads(build.execute(cluster_inventory.QUERY)), image)
            if rebuilt['refusals'] or comparable(rebuilt, closed) != comparable(inventory, closed):
                raise RebuildError('The rebuilt cluster does not hold the roles the run recorded')
            docker('stop', container)
            record = {**record, 'phase': 'completed', 'finished_at': upgrade.now()}
        except Exception as error:
            # Fixed sentences are recorded; anything else only by its kind, never its text.
            record = {**record, 'phase': 'failed', 'failure': str(error) if isinstance(error, (RebuildError, PlanError))
                      else type(error).__name__, 'finished_at': upgrade.now()}
            raise
        finally:
            # The build container is ours and holds nothing the volume does not; the volume stays.
            docker('rm', '-f', container, check=False)
            save(record)
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description='Rebuild the database cluster from one backup run onto a new volume')
    parser.add_argument('run', help='the backup run time, as lab/backup.py list prints it')
    parser.add_argument('--image-at', dest='commit', help='use the PostgreSQL lock of this version instead of the current one')
    args = parser.parse_args(argv)
    try:
        record = rebuild(args.run, args.commit)
    except (RebuildError, backup.BackupError, PlanError) as error:
        print(f'refused: {error}', file=sys.stderr)
        return 1
    except Exception as error:  # noqa: BLE001: no traceback with private detail; the record has the kind
        print(f'failed: {type(error).__name__}; see {RECORD}', file=sys.stderr)
        return 1
    print(f"Rebuilt run {record['run']} onto {record['volume']}.")
    if record['closed_logins']:
        print('Created closed (turn them on again): ' + ', '.join(record['closed_logins']))
    print('Take it into use with: /usr/bin/python3 lab/migrate-generation.py ... --to-volume ' + record['volume'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
