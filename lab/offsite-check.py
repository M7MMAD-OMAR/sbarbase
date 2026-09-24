"""Off-site copy check: a backup goes to S3-compatible storage encrypted and comes back to restore.

Usage: /usr/bin/python3 lab/offsite-check.py --endpoint URL --bucket NAME [--evidence PATH]
(with OFFSITE_KEY_ID and OFFSITE_SECRET in the environment; CI starts a throwaway MinIO)

Needs a running installation with one published environment. It configures off-site copies
through lab/offsite.py (settings on stdin), takes a backup with lab/backup.py, which copies
it off the server by itself, checks that what the storage holds is encrypted, removes the
local backup, fetches it back, restores it with lab/backup.py, and checks that a wrong
passphrase cannot read the copy.
"""
import argparse
import datetime
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

import backup
import offsite

ROOT = Path(__file__).resolve().parent.parent
checks = []
started = time.time()


def record(check, ok, detail=''):
    checks.append({'check': check, 'ok': bool(ok), 'detail': detail})
    print(('ok:   ' if ok else 'FAIL: ') + check + (f'  {detail}' if detail else ''), flush=True)
    return ok


def run(*argv, stdin=None):
    return subprocess.run(['/usr/bin/python3', *argv], cwd=ROOT, input=stdin, capture_output=True, text=True, timeout=1800)


def create_bucket(endpoint, bucket, key_id, secret):
    url = f'{endpoint}/{bucket}'
    headers = offsite.signed_headers('PUT', url, 'us-east-1', key_id, secret, {'content-length': '0'})
    parsed = urllib.parse.urlsplit(url)
    connection = http.client.HTTPConnection(parsed.netloc, timeout=30)
    connection.request('PUT', parsed.path, body=b'', headers=headers)
    status = connection.getresponse().status
    connection.close()
    return status


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--endpoint', required=True)
    parser.add_argument('--bucket', required=True)
    parser.add_argument('--evidence', default='docs/evidence/offsite-checks.json')
    parser.add_argument('--peer', default='minio', help='the S3-compatible storage the check runs against')
    args = parser.parse_args()
    key_id, secret = os.environ['OFFSITE_KEY_ID'], os.environ['OFFSITE_SECRET']
    try:
        status = create_bucket(args.endpoint, args.bucket, key_id, secret)
        record('the storage has a bucket for the copies', status in (200, 409), f'status {status}')
        environments = backup.environments()
        if not record('a published environment exists', bool(environments)):
            return finish(args.evidence, args.peer)
        e = environments[0]
        passphrase = 'ci ' + os.urandom(12).hex()
        settings = {'endpoint': args.endpoint, 'bucket': args.bucket, 'region': 'us-east-1', 'access_key_id': key_id,
                    'secret_access_key': secret, 'passphrase': passphrase, 'keep': 5}
        configured = run('lab/offsite.py', 'configure', stdin=json.dumps(settings))
        record('off-site copies are configured and proven with a test object', configured.returncode == 0,
               (configured.stdout + configured.stderr).strip()[:200])
        config_file = ROOT / '.secrets' / 'offsite.json'
        record('the settings are private', config_file.exists() and config_file.stat().st_mode & 0o077 == 0)

        made = run('lab/backup.py', 'create', e)
        stamp = backup.complete_backups(e)[-1].name
        copied = re.search(r'copied (\d+) backup\(s\) off the server', made.stdout)
        # Earlier backups not copied yet go too, so the count can be more than one.
        record('a backup copies itself off the server', made.returncode == 0 and copied and int(copied.group(1)) >= 1
               and stamp in run('lab/offsite.py', 'list', e).stdout,
               (made.stdout + made.stderr).strip().replace('\n', '; ')[:240])
        listed = run('lab/offsite.py', 'list', e)
        record('the copy is listed in the bucket', f'{e}  {stamp}' in listed.stdout, listed.stdout.strip()[:200])
        config = offsite.load_config()
        bucket = offsite.Bucket(config)
        stored = bucket.request('GET', f"{config['prefix']}/{e}/{stamp}/database.dump.sbb")
        local = (backup.BACKUPS / e / stamp / 'database.dump').read_bytes()
        record('the storage holds only ciphertext', stored.startswith(offsite.MAGIC) and b'PGDMP' not in stored[:4096]
               and local[:5] == b'PGDMP', f'{len(stored)} bytes stored for {len(local)} bytes of dump')

        shutil.rmtree(backup.BACKUPS / e / stamp)
        wrong = dict(config, passphrase='not the passphrase at all')
        try:
            offsite.fetch(e, stamp, wrong)
            record('a wrong passphrase cannot read the copy', False, 'fetched')
        except offsite.OffsiteError as error:
            record('a wrong passphrase cannot read the copy', 'decrypt' in str(error), str(error))
        record('a failed fetch leaves nothing behind', not (backup.BACKUPS / e / stamp).exists())
        fetched = run('lab/offsite.py', 'fetch', e, stamp)
        record('the copy comes back and matches its manifest', fetched.returncode == 0 and (backup.BACKUPS / e / stamp / 'manifest.json').exists(),
               (fetched.stdout + fetched.stderr).strip()[:240])
        restored = run('lab/backup.py', 'restore', e, stamp)
        record('the fetched backup restores the environment', restored.returncode == 0, (restored.stdout + restored.stderr).strip()[:240])
        run('lab/backup.py', 'discard-previous', e)
        again = run('lab/offsite.py', 'push', e)
        record('a backup already off the server is not copied again', again.returncode == 0 and 'copied 0 backup(s)' in again.stdout,
               again.stdout.strip()[:200])
    except Exception as error:
        record('off-site check ran to the end', False, f'{type(error).__name__}: {error}')
    finally:
        # The storage is throwaway; later backups on this installation stay local.
        (ROOT / '.secrets' / 'offsite.json').unlink(missing_ok=True)
        offsite.RECORD.unlink(missing_ok=True)
    return finish(args.evidence, args.peer)


def finish(evidence, peer='minio'):
    passed = bool(checks) and all(row['ok'] for row in checks)
    Path(evidence).write_text(json.dumps({
        'check': 'offsite', 'recorded': datetime.datetime.now(datetime.UTC).isoformat(timespec='seconds'), 'passed': passed,
        'count': len(checks), 'seconds': round(time.time() - started),
        'scope': f'Encrypted off-site copies for one environment on a running installation, against a throwaway {peer} as the '
                 'S3-compatible storage: configured from stdin, a backup copied off the server by itself, only ciphertext in the '
                 'bucket, the local backup removed, a wrong passphrase refused, the copy fetched and restored.',
        'checks': checks}, indent=2) + '\n')
    print(f'evidence: {evidence}\noff-site check: {"passed" if passed else "failed"}')
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
