"""Upgrade check: real upgrades, broken ones that move back by themselves, and a refused tag.

Usage:
  /usr/bin/python3 lab/upgrade-check.py candidates                 on the host, in a disposable checkout
  /usr/bin/python3 lab/upgrade-check.py before base=C good=C bad=C [migrates=C] [unhealthy=C]
                                                                    inside the running installation
  /usr/bin/python3 lab/upgrade-check.py after STAGE [--back-to base|good]
                                                                    after the restart that ends a stage
  sudo /usr/bin/python3 lab/upgrade-check.py hold                  on the host, right after the restart
                                                                    onto `unhealthy`
  /usr/bin/python3 lab/upgrade-check.py unsigned                   inside the installation; no restart
  /usr/bin/python3 lab/upgrade-check.py evidence PATH STAGE...     the evidence file; each STAGE must have run

`candidates` lists a throwaway release signing key in deploy/release-signers, commits the tree
as it is (`base`), then makes versions on top of it and prints them as shell assignments:
  good       PostgREST moves to a newer upstream release
  bad        (on good) the PostgREST pin names an image that never answers on PostgREST's port
  migrates   (on good) the control catalog schema goes up one step with an added empty table,
             and the console process stops right after opening, so the catalog was migrated
             and the new version never becomes healthy
  unhealthy  (on good) everything starts, but the console's /health always answers 503, so
             the post-start health checks never pass
The stages `after` checks:
  upgraded               onto good, confirmed
  operator-rollback      a manual `upgrade.py rollback` after a confirmed upgrade
  rolled-back            bad, moved back by itself
  migration-rolled-back  migrates, moved back by itself with the control catalog restored
  health-rolled-back     unhealthy, moved back by itself after its health deadline
`hold` watches the confirmation window of `unhealthy` from outside the container and records
that application traffic was answered 503. `unsigned` serves release tags from a local bare
repository: one signed by the listed key (accepted), one annotated but unsigned, and one signed
by a key that is not listed, and checks that `upgrade.py channel` and `start --release` refuse
the last two and that nothing moves.

Every stage compares users, identities, buckets and files in every environment, and the
control catalog's schema version and a digest of its organizations, projects, environments and
memberships, with what `before` recorded. Progress is kept in .lab/upgrade-check.json; the
evidence file is written once, by `evidence`, because a changed tracked file would make the
next `upgrade.py start` refuse. It changes the checkout, so it belongs on a throwaway machine
such as a CI runner or the rehearsal VM.
"""
import contextlib
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECORD = ROOT / '.lab' / 'upgrade-check.json'
UPGRADES = ROOT / '.lab' / 'upgrades'
UPSTREAM = ROOT / '.lab' / 'upstream'
CATALOG = UPSTREAM / 'control.sqlite'
# Written by the `migrates` candidate's console just before it stops: the schema it migrated to.
MIGRATED = ROOT / '.lab' / 'upgrade-check-migrated.json'
PROBE_TABLE = 'upgrade_check_probe'
NAMES = ('base', 'good', 'bad', 'migrates', 'unhealthy')
# Catalog rows no restart, upgrade or way back may change.
STABLE = ('organizations', 'projects', 'environments', 'memberships', 'installation_bootstrap')
HELD = 'confirming an upgrade'
# The next PostgREST release after the pinned v14.15, by its index digest.
NEWER_REST = {'tag': 'public.ecr.aws/supabase/postgrest:v14.16',
              'id': 'sha256:bea1c76a856fa39d1e542d25911cf95d02fe2bf971992d033044ff209f1504b8',
              'digests': ['public.ecr.aws/supabase/postgrest@sha256:bea1c76a856fa39d1e542d25911cf95d02fe2bf971992d033044ff209f1504b8']}
SCHEMA = re.compile(r'export const CATALOG_SCHEMA_VERSION=(\d+);')
SCHEMA_ANCHOR = '      this.db.exec(`PRAGMA user_version=${CATALOG_SCHEMA_VERSION}`);\n'
SERVER_ANCHOR = 'const app=openUpstreamApplication();\n'
STOP_AFTER_MIGRATING = (
    '// Upgrade check candidate: record the catalog schema this version migrated to, then stop.\n'
    f"await Bun.write('.lab/{MIGRATED.name}',JSON.stringify({{version:app.catalog.schemaVersion()}}));\n"
    "console.error('Upgrade check: this version stops right after migrating the control catalog');\n"
    'process.exit(1);\n')
HEALTH_ANCHOR = "return Response.json({status:'ok',held:held()},{headers});"
NEVER_HEALTHY = "return Response.json({status:'upgrade check: never healthy',held:held()},{status:503,headers});"
DESCRIPTIONS = {
    'upgraded': 'An upgrade to a version with a newer PostgREST (v14.15 to v14.16) through lab/upgrade.py and a restart.',
    'operator-rollback': 'A manual rollback by the operator after that upgrade was confirmed.',
    'rolled-back': 'An upgrade to a version whose PostgREST never answers, which the supervisor moved back by itself.',
    'migration-rolled-back': 'An upgrade to a version that migrates the control catalog and then stops, which the supervisor '
                             'moved back by itself, restoring the catalog snapshot.',
    'hold': 'Application traffic answered 503 while a version waited for its health checks.',
    'health-rolled-back': 'An upgrade to a version that starts but never passes its health checks, which the supervisor '
                          'moved back by itself once the deadline passed.',
    'unsigned': 'Release tags from a local repository: one signed by the listed key accepted, an unsigned one and one '
                'signed by an unlisted key refused, with nothing moved.',
}


def git(*args, cwd=None, env=None, stdin=None):
    return subprocess.run(['git', *args], cwd=cwd or ROOT, check=True, capture_output=True, text=True, env=env,
                          input=stdin).stdout.strip()


# Candidate versions.

def replace_once(path, old, new):
    """Replaces one exact anchor, or stops: a candidate built on a moved anchor would test nothing."""
    text = path.read_text()
    if text.count(old) != 1:
        raise SystemExit(f'upgrade check: {path.name} no longer contains {old.strip()!r} exactly once; update lab/upgrade-check.py')
    path.write_text(text.replace(old, new))


def newer_rest(root=None):
    set_rest(root or ROOT, NEWER_REST)


def never_answers(root=None):
    # postgres-meta is pinned and pulled already, and never listens on PostgREST's port.
    root = root or ROOT
    set_rest(root,json.loads((root / 'lab' / 'studio-image.lock.json').read_text())['meta'])


def set_rest(root, rest):
    path = root / 'lab' / 'images.lock.json'
    lock = json.loads(path.read_text())
    lock['rest'] = rest
    path.write_text(json.dumps(lock, indent=2) + '\n')


def migrate_then_stop(root=None):
    """One more catalog schema step (an empty table, nothing else), and a console that stops once
    it has opened, and so migrated, the catalog."""
    root = root or ROOT
    path = root / 'src' / 'control' / 'catalog.ts'
    found = SCHEMA.findall(path.read_text())
    if len(found) != 1:
        raise SystemExit('upgrade check: catalog.ts no longer declares CATALOG_SCHEMA_VERSION once; update lab/upgrade-check.py')
    version = int(found[0]) + 1
    replace_once(path, f'export const CATALOG_SCHEMA_VERSION={found[0]};', f'export const CATALOG_SCHEMA_VERSION={version};')
    replace_once(path, SCHEMA_ANCHOR,
                 f"      if(version<{version})this.db.exec('CREATE TABLE IF NOT EXISTS {PROBE_TABLE}(id INTEGER PRIMARY KEY)');\n"
                 + SCHEMA_ANCHOR)
    replace_once(root / 'lab' / 'upstream-server.ts', SERVER_ANCHOR, SERVER_ANCHOR + STOP_AFTER_MIGRATING)


def never_healthy(root=None):
    replace_once((root or ROOT) / 'src' / 'http' / 'health.ts', HEALTH_ANCHOR, NEVER_HEALTHY)


def derive(parent, message, edit):
    git('checkout', '-q', '--detach', parent)
    edit()
    git('commit', '-q', '-am', message)
    return git('rev-parse', 'HEAD')


def signer_key():
    """The throwaway release signing key of this run. It lives in the git directory: inside the
    checkout (so the container sees it at the same path), owned by the checkout's owner, and never
    committed."""
    return Path(git('rev-parse', '--absolute-git-dir')) / 'upgrade-check' / 'signer'


def list_signer():
    """Lists a throwaway key in deploy/release-signers, so the channel checks each tag's own
    signature instead of refusing every release because no key is listed."""
    key = signer_key()
    key.parent.mkdir(mode=0o700, exist_ok=True)
    for path in (key, key.parent / 'signer.pub'):
        path.unlink(missing_ok=True)
    subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'upgrade-check', '-f', str(key)],
                   check=True, capture_output=True)
    with (ROOT / 'deploy' / 'release-signers').open('a') as handle:
        handle.write(f'upgrade-check@example.com namespaces="git" {(key.parent / "signer.pub").read_text().strip()}\n')


def candidates():
    git('config', 'user.email', 'ci@example.com')
    git('config', 'user.name', 'ci')
    list_signer()
    git('add', '-A')
    git('commit', '-q', '--allow-empty', '-m', 'Upgrade check: the version running now')
    base = git('rev-parse', 'HEAD')
    good = derive(base, 'Upgrade check: PostgREST v14.16', newer_rest)
    bad = derive(good, 'Upgrade check: a PostgREST pin that never answers', never_answers)
    migrates = derive(good, 'Upgrade check: a catalog migration, then a console that stops', migrate_then_stop)
    unhealthy = derive(good, 'Upgrade check: a console whose health check never passes', never_healthy)
    git('checkout', '-q', '--detach', base)
    print(f'base={base}\ngood={good}\nbad={bad}\nmigrates={migrates}\nunhealthy={unhealthy}')


# Observation. Only the standard library at module level: `hold` runs on the host.

def catalog_state(path=None):
    """The catalog's schema version, its tables, and a digest of the rows nothing may change."""
    with contextlib.closing(sqlite3.connect(f'file:{path or CATALOG}?mode=ro', uri=True, timeout=30)) as database:
        version = database.execute('PRAGMA user_version').fetchone()[0]
        tables = sorted(row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'"))
        digest, rows = hashlib.sha256(), {}
        for table in STABLE:
            if table not in tables:
                continue
            found = sorted(repr(row) for row in database.execute(f'SELECT * FROM {table}'))
            rows[table] = len(found)
            digest.update(f'{table}\n'.encode() + '\n'.join(found).encode() + b'\n')
    return {'version': version, 'tables': tables, 'rows': rows, 'digest': digest.hexdigest()}


def fetch(url):
    """(status, body) of one GET; (None, reason) when nothing answers. No proxy from the environment."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=5) as response:
            return response.status, response.read().decode(errors='replace')
    except urllib.error.HTTPError as error:
        with error:
            return error.code, error.read().decode(errors='replace')
    except (OSError, ValueError) as error:
        return None, error.__class__.__name__


def console():
    try:
        return json.loads((UPSTREAM / 'server.json').read_text())['url'].rstrip('/')
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def held(url):
    """True when an application request is answered with the upgrade hold."""
    status, body = fetch(url + '/rest/v1/')
    return status == 503 and HELD in body


def upgrade_state():
    try:
        return json.loads((UPGRADES / 'state.json').read_text())
    except (OSError, ValueError):
        return {}


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
    healthy, health, url = False, None, None
    for _ in range(30):
        url = console()
        health = fetch(url + '/health')[0] if url else None
        if install_server.smoke() and health == 200:
            healthy = True
            break
        time.sleep(2)
    status = subprocess.run(['/usr/bin/python3', 'lab/upgrade.py', 'status'], cwd=ROOT, capture_output=True, text=True)
    return {'head': git('rev-parse', 'HEAD'), 'rest_tag': lock['rest']['tag'], 'environments': environments,
            'healthy': healthy, 'console_health': health, 'held': held(url) if url else None,
            'catalog': catalog_state(), 'upgrade': upgrade_state(), 'status': status.stdout}


# The record: every stage appends its checks; `evidence` writes the file once.

def load():
    return json.loads(RECORD.read_text())


def recorder(stage, record):
    rows = []

    def check(name, ok, detail=''):
        rows.append({'stage': stage, 'check': name, 'ok': bool(ok), 'detail': str(detail)})
        print(('ok:   ' if ok else 'FAIL: ') + name + (f'  {detail}' if detail else ''), flush=True)

    def finish():
        record.setdefault('checks', []).extend(rows)
        RECORD.write_text(json.dumps(record))
        return 0 if all(row['ok'] for row in rows) else 1
    return check, finish


def commits(pairs):
    found = {}
    for pair in pairs:
        name, _, commit = pair.partition('=')
        if name not in NAMES or not re.fullmatch(r'[0-9a-f]{40}', commit):
            raise SystemExit(f'upgrade check: expected name=commit with a name in {NAMES}, got {pair!r}')
        found[name] = commit
    for name in ('base', 'good', 'bad'):
        if name not in found:
            raise SystemExit(f'upgrade check: `before` needs {name}=COMMIT')
    return found


def before(pairs):
    names = commits(pairs)
    MIGRATED.unlink(missing_ok=True)
    RECORD.write_text(json.dumps({'commits': names, 'observed': observe()}))
    print('recorded the installation before the upgrade')
    return 0


def schema_at(commit):
    found = SCHEMA.search(git('show', f'{commit}:src/control/catalog.ts'))
    return int(found.group(1)) if found else None


# Which candidate each automatic way back is from.
MOVED_BACK = {'rolled-back': 'bad', 'migration-rolled-back': 'migrates', 'health-rolled-back': 'unhealthy'}


def after(stage, back_to):
    record = load()
    names, then = record['commits'], record['observed']
    now = observe()
    check, finish = recorder(stage, record)
    state = now['upgrade']
    moved = f"{state.get('phase')} automatic={state.get('automatic')} to={str(state.get('to'))[:12]}"
    rest = now['environments'] and all(value['rest_on_pin'] for value in now['environments'].values())
    if stage == 'upgraded':
        check('the upgrade finished and was confirmed', state.get('phase') == 'confirmed' and state.get('to') == names['good'], moved)
        check('the checkout is on the new version', now['head'] == names['good'], now['head'][:12])
        check('every REST container runs the new PostgREST', rest, now['rest_tag'])
    elif stage == 'operator-rollback':
        check('the operator moved back from the confirmed upgrade', state.get('phase') == 'rolled_back'
              and state.get('automatic') is False and state.get('to') == names['good'], moved)
        check('the checkout is on the version before the upgrade', now['head'] == names['base'] == state.get('from'), now['head'][:12])
        check('every REST container runs that version\'s PostgREST again', rest, now['rest_tag'])
        check('after confirmation the control state was kept, not restored', not state.get('restored'), state.get('restored'))
    else:
        candidate, target = MOVED_BACK[stage], names[back_to]
        check('the broken version was moved back automatically', state.get('phase') == 'rolled_back'
              and state.get('automatic') is True and state.get('to') == names[candidate], moved)
        check('the checkout is on the last good version', now['head'] == target == state.get('from'), now['head'][:12])
        check('every REST container runs the last good PostgREST again', rest, now['rest_tag'])
        check('the control state snapshot taken when the new version started was restored',
              state.get('attempted_at') and state.get('snapshot') and state.get('restored') == state.get('snapshot'),
              state.get('restored'))
        if stage == 'migration-rolled-back':
            try:
                migrated = json.loads(MIGRATED.read_text()).get('version')
            except (OSError, ValueError, AttributeError):
                migrated = None
            wanted = schema_at(names['migrates'])
            check('the new version had migrated the control catalog before it stopped',
                  migrated == wanted and wanted is not None and wanted > then['catalog']['version'],
                  f'catalog schema {then["catalog"]["version"]} -> {migrated} (the release declares {wanted})')
            check('the table that migration added is gone again', PROBE_TABLE not in now['catalog']['tables'])
    line = next((row for row in now['status'].splitlines() if row.startswith('result')), '')
    expected = 'confirmed' if stage == 'upgraded' else 'rolled_back'
    check(f'upgrade.py status reports {expected}', line.split()[1:2] == [expected + ':'], line)
    check('the control catalog is at the schema it had before the upgrade', now['catalog']['version'] == then['catalog']['version'],
          f"{then['catalog']['version']} -> {now['catalog']['version']}")
    check('organizations, projects, environments and memberships in the control catalog are unchanged',
          now['catalog']['digest'] == then['catalog']['digest'], json.dumps(now['catalog']['rows'], sort_keys=True))
    same = all(now['environments'].get(e, {}).get('counts') == values['counts'] for e, values in then['environments'].items())
    check('users, identities, buckets and files are unchanged in every environment',
          same and len(now['environments']) == len(then['environments']), f"{len(now['environments'])} environment(s)")
    check('Auth, REST and the console answer, and the console /health answers 200', now['healthy'], f"/health {now['console_health']}")
    check('application traffic is served, not held', now['held'] is False, f"held={now['held']}")
    return finish()


def hold(limit=600):
    """Watches the confirmation window of `unhealthy` from the host (the container restarts under
    it): an application request is answered 503, /health fails, the catalog keeps its schema."""
    record = load()
    names, then = record['commits'], record['observed']
    check, finish = recorder('hold', record)
    target = names.get('unhealthy')
    until = time.monotonic() + limit
    seen = None
    while time.monotonic() < until:
        state = upgrade_state()
        if state.get('to') == target and state.get('phase') not in (None, 'applied'):
            break
        url = console()
        if state.get('to') == target and state.get('phase') == 'applied' and (UPGRADES / 'hold').exists() and url and held(url):
            again = upgrade_state()
            if again.get('to') == target and again.get('phase') == 'applied':
                seen = {'health': fetch(url + '/health')[0], 'catalog': catalog_state()['version']}
                break
        time.sleep(1)
    check('application traffic was answered 503 while the new version waited for its health checks', seen is not None,
          'observed' if seen else f"not observed; phase {upgrade_state().get('phase')}")
    if seen:
        check('the new version\'s /health failed during that window', seen['health'] == 503, f"/health {seen['health']}")
        check('the control catalog kept its schema during that window', seen['catalog'] == then['catalog']['version'],
              f"{then['catalog']['version']} -> {seen['catalog']}")
    return finish()


# Release tags the channel must refuse.

def unchanged():
    """What a refused `start --release` must leave as it was."""
    def listing(path):
        return sorted(str(item.relative_to(ROOT)) for item in path.glob('*/*')) if path.is_dir() else []
    state = UPGRADES / 'state.json'
    return {'head': git('rev-parse', 'HEAD'), 'tracked changes': git('status', '--porcelain', '--untracked-files=no'),
            'refs': git('for-each-ref', '--format=%(refname) %(objectname)', 'refs/heads', 'refs/tags', 'refs/remotes'),
            'upgrade state': hashlib.sha256(state.read_bytes()).hexdigest() if state.exists() else None,
            'intent': (UPSTREAM / 'upgrade-intent.json').exists(), 'hold': (UPGRADES / 'hold').exists(),
            'snapshots': sorted(item.name for item in (UPGRADES / 'snapshots').glob('*')), 'backups': listing(ROOT / '.lab' / 'backups')}


def unsigned():
    record = load()
    check, finish = recorder('unsigned', record)
    key = signer_key()
    current = json.loads(git('show', 'HEAD:release.json'))['version']
    tags = {'signed': 'v9.9.7', 'unsigned': 'v9.9.9', 'stranger': 'v9.9.10'}
    if not key.is_file() or tuple(int(part) for part in current.split('-')[0].split('.')) >= (9, 9, 7):
        check('the throwaway signing key exists and the installation is older than the test tags', False, current)
        return finish()
    available = UPGRADES / 'available.json'
    kept = available.read_bytes() if available.exists() else None
    start = unchanged()
    isolated = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_TERMINAL_PROMPT': '0'}
    identity = ('-c', 'user.name=upgrade-check', '-c', 'user.email=ci@example.com')
    try:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / 'releases.git'
            git('init', '-q', '--bare', str(source), cwd=base, env=isolated)
            # The installation's own objects, so a release commit is only its new manifest.
            objects = git('rev-parse', '--path-format=absolute', '--git-path', 'objects')
            (source / 'objects' / 'info' / 'alternates').write_text(objects + '\n')
            stranger = base / 'stranger'
            subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'stranger', '-f', str(stranger)],
                           check=True, capture_output=True)
            tree, head = git('rev-parse', 'HEAD^{tree}'), git('rev-parse', 'HEAD')

            def publish(tag, signing):
                """A commit with this checkout's tree and a release.json for the tag, tagged in the
                source. Its parent is HEAD, so the release contains this checkout (the channel refuses
                one that does not); only HEAD's own object is needed, which a shallow CI checkout has."""
                manifest = {'version': tag[1:], 'minimum_from': current, 'migrations': [],
                            'notes': {'en': f'Upgrade check release {tag}.', 'ar': f'إصدار فحص الترقية {tag}.'}}
                index = {**isolated, 'GIT_INDEX_FILE': str(base / 'index')}
                blob = git('hash-object', '-w', '--stdin', cwd=source, env=isolated,
                           stdin=json.dumps(manifest, indent=2, ensure_ascii=False) + '\n')
                git('read-tree', tree, cwd=source, env=index)
                git('update-index', '--add', '--cacheinfo', f'100644,{blob},release.json', cwd=source, env=index)
                commit = git(*identity, 'commit-tree', git('write-tree', cwd=source, env=index), '-p', head,
                             '-m', f'Release {tag}', cwd=source, env=isolated)
                if signing:
                    git(*identity, '-c', 'gpg.format=ssh', '-c', f'user.signingkey={signing}', 'tag', '-s', tag, '-m', tag,
                        commit, cwd=source, env=isolated)
                else:
                    git(*identity, 'tag', '-a', tag, '-m', tag, commit, cwd=source, env=isolated)

            environment = {**os.environ, 'SBARBASE_RELEASE_SOURCE': str(source)}

            def upgrade(*args):
                return subprocess.run(['/usr/bin/python3', 'lab/upgrade.py', *args], cwd=ROOT, env=environment,
                                      capture_output=True, text=True)

            def channel():
                result = upgrade('channel', '--json')
                try:
                    found = json.loads(result.stdout) if result.returncode == 0 else {}
                except ValueError:
                    found = {}
                return found.get('available') or {}, found.get('refusals'), found.get('newest') or {}, result.stderr.strip()

            publish(tags['signed'], key)
            offered, refusals, _, error = channel()
            check('the channel accepts a tag signed by the listed key', offered.get('tag') == tags['signed']
                  and offered.get('signed') is True and offered.get('class') == 'safe' and refusals == [],
                  f"{offered.get('tag')} signed={offered.get('signed')} refusals={refusals} {error}")
            publish(tags['unsigned'], None)
            offered, refusals, newest, error = channel()
            named = f"{tags['unsigned']} is not signed by a key listed in release-signers"
            # The newer unsigned tag is named, with its reason, and the signed one stays on offer.
            check('the channel names an unsigned tag as not signed and never offers it', newest.get('tag') == tags['unsigned']
                  and newest.get('signed') is False and any(named in item for item in newest.get('reasons') or [])
                  and offered.get('tag') == tags['signed'] and refusals == [],
                  f"newest {newest.get('tag')} signed={newest.get('signed')} reasons={newest.get('reasons')} "
                  f"offered {offered.get('tag')} refusals={refusals} {error}")
            result = upgrade('start', '--release', tags['unsigned'])
            check('upgrade.py start --release refuses the unsigned tag, naming its signature', result.returncode == 1
                  and named in result.stderr and 'Nothing was changed' in result.stderr, result.stderr.strip()[-300:])
            publish(tags['stranger'], stranger)
            result = upgrade('start', '--release', tags['stranger'])
            named = f"{tags['stranger']} is not signed by a key listed in release-signers"
            check('upgrade.py start --release refuses a tag signed by a key that is not listed', result.returncode == 1
                  and named in result.stderr and 'Nothing was changed' in result.stderr, result.stderr.strip()[-300:])
    finally:
        # The channel fetched the test tags into its own namespace; the check result named them.
        for tag in tags.values():
            subprocess.run(['git', 'update-ref', '-d', f'refs/sbarbase-releases/tags/{tag}'], cwd=ROOT, capture_output=True)
        if kept is None:
            available.unlink(missing_ok=True)
        else:
            available.write_bytes(kept)
    end = unchanged()
    check('nothing moved: checkout, branches and tags, upgrade state, snapshots, backups and the hold', end == start,
          ', '.join(name for name in start if start[name] != end[name]) or 'all as they were')
    return finish()


def evidence(path, required):
    if not RECORD.exists():
        print('upgrade check: nothing was recorded, so no evidence is written', file=sys.stderr)
        return 1
    rows = load().get('checks', [])
    stages = list(dict.fromkeys(row['stage'] for row in rows))
    missing = [stage for stage in required if stage not in stages]
    passed = bool(rows) and all(row['ok'] for row in rows) and not missing
    Path(path).write_text(json.dumps({
        'check': 'upgrade', 'recorded': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'passed': passed,
        'count': len(rows), 'stages': stages, 'missing': missing,
        'scope': 'One installation with its environments on a clean machine, not a real server. '
                 + ' '.join(DESCRIPTIONS.get(stage, stage) for stage in stages)
                 + ' Data is compared by row counts, and the control catalog by its schema version and a digest of its '
                   'organizations, projects, environments and memberships, not every table.',
        'checks': rows}, indent=2, ensure_ascii=False) + '\n')
    print(f'evidence: {path}' + (f" (missing stages: {', '.join(missing)})" if missing else ''))
    return 0 if passed else 1


def main(argv):
    command = argv[:1]
    if command == ['candidates'] and len(argv) == 1:
        candidates()
        return 0
    if command == ['before'] and len(argv) >= 4:
        return before(argv[1:])
    if command == ['after'] and len(argv) in (2, 4) and argv[1] in ('upgraded', 'operator-rollback', *MOVED_BACK):
        back_to = 'good'
        if len(argv) == 4:
            if argv[2] != '--back-to' or argv[3] not in ('base', 'good'):
                print(__doc__, file=sys.stderr)
                return 2
            back_to = argv[3]
        return after(argv[1], back_to)
    if command == ['hold'] and len(argv) == 1:
        return hold()
    if command == ['unsigned'] and len(argv) == 1:
        return unsigned()
    if command == ['evidence'] and len(argv) >= 2:
        return evidence(argv[1], argv[2:])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
