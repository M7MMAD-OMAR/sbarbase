"""Upgrade check: a real upgrade, then a broken one that moves back by itself.

Usage:
  /usr/bin/python3 lab/upgrade-check.py candidates             on the host, in a disposable checkout
  /usr/bin/python3 lab/upgrade-check.py before GOOD BAD        inside the running installation
  /usr/bin/python3 lab/upgrade-check.py after upgraded         after the restart onto the good candidate
  /usr/bin/python3 lab/upgrade-check.py after rolled-back --evidence PATH

`candidates` commits the tree as it is, then makes two versions on top of it: one that
moves PostgREST to a newer upstream release, and one whose PostgREST pin names an image
that never answers on PostgREST's port, as a broken release would. It prints both commits
as shell assignments. The live steps compare users, files and buckets in every environment
before and after, the image each REST container runs, and the service health checks.
It changes the checkout, so it belongs on a throwaway machine such as a CI runner.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECORD = ROOT / '.lab' / 'upgrade-check.json'
# The next PostgREST release after the pinned v14.15, by its index digest.
NEWER_REST = {'tag': 'public.ecr.aws/supabase/postgrest:v14.16',
              'id': 'sha256:bea1c76a856fa39d1e542d25911cf95d02fe2bf971992d033044ff209f1504b8',
              'digests': ['public.ecr.aws/supabase/postgrest@sha256:bea1c76a856fa39d1e542d25911cf95d02fe2bf971992d033044ff209f1504b8']}


def git(*args):
    return subprocess.run(['git', *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def candidate(base, message, rest):
    git('checkout', '-q', '--detach', base)
    path = ROOT / 'lab' / 'images.lock.json'
    lock = json.loads(path.read_text())
    lock['rest'] = rest
    path.write_text(json.dumps(lock, indent=2) + '\n')
    git('commit', '-q', '-am', message)
    return git('rev-parse', 'HEAD')


def candidates():
    git('config', 'user.email', 'ci@example.com')
    git('config', 'user.name', 'ci')
    git('add', '-A')
    git('commit', '-q', '--allow-empty', '-m', 'Upgrade check: the version running now')
    base = git('rev-parse', 'HEAD')
    good = candidate(base, 'Upgrade check: PostgREST v14.16', NEWER_REST)
    # postgres-meta is pinned and pulled already, and never listens on PostgREST's port.
    meta = json.loads((ROOT / 'lab' / 'studio-image.lock.json').read_text())['meta']
    bad = candidate(good, 'Upgrade check: a PostgREST pin that never answers', meta)
    git('checkout', '-q', '--detach', base)
    print(f'base={base}\ngood={good}\nbad={bad}')


def observe():
    sys.path.insert(0, str(ROOT / 'lab'))
    import backup
    import install_server
    import run as lab
    lock = json.loads((ROOT / 'lab' / 'images.lock.json').read_text())
    expected = json.loads(lab.docker('image', 'inspect', lock['rest']['id']).stdout)[0]['Id']
    environments = {}
    for e in backup.environments():
        container = json.loads(lab.docker('inspect', f'sbarbase-durable-{e}-rest').stdout)[0]
        environments[e] = {'counts': backup.counts(e), 'rest_on_pin': container['Image'] == expected}
    healthy = False
    for _ in range(30):
        if install_server.smoke():
            healthy = True
            break
        time.sleep(2)
    upgrade = json.loads((ROOT / '.lab' / 'upgrades' / 'state.json').read_text()) if (ROOT / '.lab' / 'upgrades' / 'state.json').exists() else {}
    return {'head': git('rev-parse', 'HEAD'), 'rest_tag': lock['rest']['tag'], 'environments': environments,
            'healthy': healthy, 'phase': upgrade.get('phase'), 'automatic': upgrade.get('automatic')}


def after(stage, evidence):
    before = json.loads(RECORD.read_text())
    now = observe()
    checks = []

    def record(check, ok, detail=''):
        checks.append({'check': check, 'ok': bool(ok), 'detail': detail})
        print(('ok:   ' if ok else 'FAIL: ') + check + (f'  {detail}' if detail else ''))

    same = all(now['environments'].get(e, {}).get('counts') == values['counts'] for e, values in before['observed']['environments'].items())
    if stage == 'upgraded':
        record('the upgrade finished and was confirmed', now['phase'] == 'confirmed', str(now['phase']))
        record('the checkout is on the new version', now['head'] == before['good'], now['head'][:12])
        record('every REST container runs the new PostgREST', now['environments'] and all(v['rest_on_pin'] for v in now['environments'].values()), now['rest_tag'])
    else:
        record('the broken version was moved back automatically', now['phase'] == 'rolled_back' and now['automatic'] is True, f"{now['phase']} automatic={now['automatic']}")
        record('the checkout is on the last good version', now['head'] == before['good'], now['head'][:12])
        record('every REST container runs the last good PostgREST again', now['environments'] and all(v['rest_on_pin'] for v in now['environments'].values()), now['rest_tag'])
    record('users, identities, buckets and files are unchanged in every environment', same and len(now['environments']) == len(before['observed']['environments']),
           f"{len(now['environments'])} environment(s)")
    record('Auth, REST and the console answer', now['healthy'])
    before.setdefault('checks', []).extend(checks)
    RECORD.write_text(json.dumps(before))
    if evidence:
        passed = all(row['ok'] for row in before['checks'])
        Path(evidence).write_text(json.dumps({
            'check': 'upgrade', 'recorded': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'passed': passed,
            'count': len(before['checks']),
            'scope': 'One installation with its environments on a clean machine: an upgrade to a version with a newer PostgREST '
                     '(v14.15 to v14.16) through lab/upgrade.py and a restart, then an upgrade to a version whose PostgREST never '
                     'starts, which the supervisor moved back by itself. Data is compared by row counts, not contents.',
            'checks': before['checks']}, indent=2) + '\n')
        print(f'evidence: {evidence}')
    if not all(row['ok'] for row in checks):
        return 1
    return 0


def main(argv):
    if argv[:1] == ['candidates']:
        candidates()
        return 0
    if argv[:1] == ['before'] and len(argv) == 3:
        RECORD.write_text(json.dumps({'good': argv[1], 'bad': argv[2], 'observed': observe()}))
        print('recorded the installation before the upgrade')
        return 0
    if argv[:1] == ['after'] and len(argv) >= 2 and argv[1] in ('upgraded', 'rolled-back'):
        evidence = argv[argv.index('--evidence') + 1] if '--evidence' in argv else None
        return after(argv[1], evidence)
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
