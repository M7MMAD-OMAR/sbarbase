"""Live check of per-environment backup and restore on a running installation.

    /usr/bin/python3 lab/backup-check.py --evidence PATH

Uses the first published environment. It writes a marker row and a Storage object, takes a
backup, then changes both and adds a user, restores the backup, and checks that everything is
exactly as it was at backup time while the environment serves again. It then discards the
state the restore set aside. It leaves a table named ``backup_probe`` and one bucket named
``backup-probe`` in that environment.
"""
import argparse
import datetime
import json
import subprocess
import sys
import uuid
from pathlib import Path
import backup
import durable_runtime as runtime

checks = []


def check(name, passed, detail=''):
    checks.append({'check': name, 'passed': bool(passed), **({'detail': detail} if detail else {})})
    print(('ok:   ' if passed else 'FAIL: ') + name + (f'  {detail}' if detail else ''))
    return passed


def storage(e, values, method, path, body=None, content_type='application/json'):
    endpoints = backup.published()[e]['storage']
    headers = {'authorization': 'Bearer ' + runtime.token(values['jwt'], 'service_role'),
               'x-forwarded-host': endpoints['tenantHost'], 'content-type': content_type}
    return runtime.http(endpoints['url'] + path, method, body, headers)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--evidence', required=True)
    args = parser.parse_args()
    environments = backup.environments()
    if not check('a published environment exists', environments):
        return finish(args.evidence, None)
    e = environments[0]
    values = json.loads((runtime.PRIVATE / 'runtime.json').read_text())['environments'][e]
    backup.sql('CREATE TABLE IF NOT EXISTS public.backup_probe(v text); TRUNCATE public.backup_probe; '
               "INSERT INTO public.backup_probe VALUES ('at-backup');", e)
    status, _ = storage(e, values, 'POST', '/bucket', json.dumps({'id': 'backup-probe', 'name': 'backup-probe'}).encode())
    check('a bucket is created through Storage', status in (200, 409), f'status {status}')
    status, _ = storage(e, values, 'POST', '/object/backup-probe/kept.txt', b'kept at backup time', 'text/plain')
    check('an object is uploaded through Storage', status == 200, f'status {status}')

    path, manifest = backup.create(e)
    check('backup completes while the environment serves', (path / 'manifest.json').is_file(), path.name)
    check('backup holds the uploaded file', manifest['objects']['files'] >= 1, f"{manifest['objects']['files']} file(s)")
    check('backup verifies against its digests', backup.verify(e, path)['runtime'] == e)

    # Changes after the backup: all of them must be gone after the restore.
    backup.sql("INSERT INTO public.backup_probe VALUES ('after-backup');", e)
    storage(e, values, 'POST', '/object/backup-probe/later.txt', b'written after the backup', 'text/plain')
    storage(e, values, 'DELETE', '/object/backup-probe/kept.txt')
    backup.sql("INSERT INTO auth.users(instance_id,id,aud,role,email,created_at,updated_at) VALUES "
               f"('00000000-0000-0000-0000-000000000000','{uuid.uuid4()}','authenticated','authenticated',"
               f"'after-{uuid.uuid4().hex[:8]}@example.com',now(),now());", e)
    check('the environment changed after the backup', backup.counts(e) != manifest['counts'])

    record = backup.restore(e, path.name)
    check('restore completes and Auth answers again', record['backup'] == path.name)
    check('rows are exactly those at backup time', backup.counts(e) == manifest['counts'])
    rows = backup.sql('SELECT string_agg(v, \',\' ORDER BY v) FROM public.backup_probe;', e)
    check('a row written after the backup is gone', rows == 'at-backup', rows)
    status, body = storage(e, values, 'GET', '/object/backup-probe/kept.txt')
    check('a file deleted after the backup is back', status == 200 and body == b'kept at backup time', f'status {status}')
    status, _ = storage(e, values, 'GET', '/object/backup-probe/later.txt')
    check('a file written after the backup is gone', status in (400, 404), f'status {status}')
    others = [name for name in backup.environments() if name != e]
    check('other environments were never stopped', all(
        runtime.inspect('container', f'{backup.PREFIX}-{name}-auth') for name in others))

    dropped = backup.discard_previous(e)
    check('the state set aside by the restore is discarded on request', dropped == [record['previous_database']])
    left = backup.sql(f"SELECT count(*) FROM pg_database WHERE datname LIKE '{e}\\_pre\\_%' ESCAPE '\\';")
    check('no previous database remains', left == '0', left)
    return finish(args.evidence, e)


def finish(evidence, e):
    passed = all(item['passed'] for item in checks)
    record = {'probe': 'lab/backup-check.py', 'run_at': datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
              'scope': 'Per-environment backup and in-place restore on one running installation: database rows, '
                       'auth users and Storage files return exactly to the backup, while other environments keep '
                       'running. Backups stay on this host; copying them elsewhere is a separate step.',
              'environment': e, 'checks': checks, 'count': len(checks), 'passed': passed}
    Path(evidence).write_text(json.dumps(record, indent=2) + '\n')
    print(f"backup check: {'passed' if passed else 'failed'} ({sum(item['passed'] for item in checks)} of {len(checks)})")
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
