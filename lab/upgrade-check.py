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
  /usr/bin/python3 lab/upgrade-check.py channel-source PATH        a local release source (after `before`)
  /usr/bin/python3 lab/upgrade-check.py channel-base OPERATOR      once the service runs `base`, pointed at it
  /usr/bin/python3 lab/upgrade-check.py channel-apply NAME OPERATOR
                                                                    publish release NAME and install it through
                                                                    the management API (or automatic mode)
  /usr/bin/python3 lab/upgrade-check.py channel-journal NAME FILE  the unit's journal lines for that release
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

The channel stages (lab/vm-milestones.sh channel, in the rehearsal VM under systemd) take the
real operator path instead of `upgrade.py start`: releases signed with the throwaway key,
published one at a time in a bare repository the service reads through SBARBASE_RELEASE_SOURCE,
found with "check now" and installed with the management API as the operator (OPERATOR is the
private operator file), or by automatic mode inside its window. See RELEASES for what each
release changes. A thread follows each install from outside the service: the drain, the
console going away, the phases, and application traffic answered 503 while the new version
waited for its health checks.

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
    expected = {name: json.loads(lab.docker('image', 'inspect', lock[name]['id']).stdout)[0]['Id'] for name in ('rest', 'auth')}

    def on_pin(container, name):
        return json.loads(lab.docker('inspect', container).stdout)[0]['Image'] == expected[name]
    environments = {}
    for e in backup.environments():
        environments[e] = {'counts': backup.counts(e), 'rest_on_pin': on_pin(f'sbarbase-durable-{e}-rest', 'rest'),
                           'auth_on_pin': on_pin(f'sbarbase-durable-{e}-auth', 'auth')}
    healthy, health, url = False, None, None
    for _ in range(30):
        url = console()
        health = fetch(url + '/health')[0] if url else None
        if install_server.smoke() and health == 200:
            healthy = True
            break
        time.sleep(2)
    return {'head': git('rev-parse', 'HEAD'), 'rest_tag': lock['rest']['tag'], 'auth_tag': lock['auth']['tag'],
            'management_auth_on_pin': on_pin('sbarbase-durable-management-auth', 'auth'), 'environments': environments,
            'healthy': healthy, 'console_health': health, 'held': held(url) if url else None,
            'catalog': catalog_state(), 'upgrade': upgrade_state()}


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
    """The catalog schema a version declares, read as lab/upgrade.py reads it for `rollback`
    (`after` runs inside the installation, where that module can be imported)."""
    sys.path.insert(0, str(ROOT / 'lab'))
    import upgrade
    return upgrade.catalog_support(commit)


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
    compare(check, then, now)
    return finish()


def compare(check, then, now):
    """What no upgrade, way back or restart may change, against what `before` recorded."""
    check('the control catalog is at the schema it had before the upgrade', now['catalog']['version'] == then['catalog']['version'],
          f"{then['catalog']['version']} -> {now['catalog']['version']}")
    check('organizations, projects, environments and memberships in the control catalog are unchanged',
          now['catalog']['digest'] == then['catalog']['digest'], json.dumps(now['catalog']['rows'], sort_keys=True))
    same = all(now['environments'].get(e, {}).get('counts') == values['counts'] for e, values in then['environments'].items())
    check('users, identities, buckets and files are unchanged in every environment',
          same and len(now['environments']) == len(then['environments']), f"{len(now['environments'])} environment(s)")
    check('Auth, REST and the console answer, and the console /health answers 200', now['healthy'], f"/health {now['console_health']}")
    check('application traffic is served, not held', now['held'] is False, f"held={now['held']}")


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

def isolated():
    """Git without the operator's own configuration and without prompts."""
    return {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_TERMINAL_PROMPT': '0'}


IDENTITY = ('-c', 'user.name=upgrade-check', '-c', 'user.email=ci@example.com')


def release_source(source):
    """A bare repository that serves release tags, borrowing the installation's own objects, so a
    release commit adds only what it changes."""
    source = Path(source)
    git('init', '-q', '--bare', str(source), cwd=source.parent, env=isolated())
    objects = git('rev-parse', '--path-format=absolute', '--git-path', 'objects')
    (source / 'objects' / 'info' / 'alternates').write_text(objects + '\n')
    return source


def publish_release(source, tag, tree, parent, signing, minimum, files=None):
    """A commit with `tree`, `files` ({path: text}) and a release.json for the tag, whose parent is
    `parent`, tagged in the source: signed with the private key at `signing`, or annotated and
    unsigned when it is None. Returns the commit."""
    environment = isolated()
    manifest = {'version': tag[1:], 'minimum_from': minimum, 'migrations': [],
                'notes': {'en': f'Upgrade check release {tag}.', 'ar': f'إصدار فحص الترقية {tag}.'}}
    index = {**environment, 'GIT_INDEX_FILE': str(Path(source) / 'upgrade-check.index')}
    git('read-tree', tree, cwd=source, env=index)
    written = {**(files or {}), 'release.json': json.dumps(manifest, indent=2, ensure_ascii=False) + '\n'}
    for path, text in written.items():
        blob = git('hash-object', '-w', '--stdin', cwd=source, env=environment, stdin=text)
        git('update-index', '--add', '--cacheinfo', f'100644,{blob},{path}', cwd=source, env=index)
    commit = git(*IDENTITY, 'commit-tree', git('write-tree', cwd=source, env=index), '-p', parent,
                 '-m', f'Release {tag}', cwd=source, env=environment)
    (Path(source) / 'upgrade-check.index').unlink(missing_ok=True)
    if signing:
        git(*IDENTITY, '-c', 'gpg.format=ssh', '-c', f'user.signingkey={signing}', 'tag', '-s', tag, '-m', tag,
            commit, cwd=source, env=environment)
    else:
        git(*IDENTITY, 'tag', '-a', tag, '-m', tag, commit, cwd=source, env=environment)
    return commit


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
    try:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = release_source(base / 'releases.git')
            stranger = base / 'stranger'
            subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'stranger', '-f', str(stranger)],
                           check=True, capture_output=True)
            tree, head = git('rev-parse', 'HEAD^{tree}'), git('rev-parse', 'HEAD')

            def publish(tag, signing):
                # Its parent is HEAD, so the release contains this checkout (the channel refuses one
                # that does not); only HEAD's own object is needed, which a shallow CI checkout has.
                publish_release(source, tag, tree, head, signing, current)

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


# The update channel's own operator path (lab/vm-milestones.sh channel): signed releases from a
# local source, asked for through the management API as the operator, carried out by the
# supervisor under systemd, and followed from outside the service.

MANAGEMENT_KEY = 'sb_publishable_sbarbase_local_management'
# GoTrue's next release after the pinned v2.196.0, by its index digest: the attended release.
NEWER_AUTH = {'tag': 'public.ecr.aws/supabase/gotrue:v2.197.0',
              'id': 'sha256:1736a63078f5922b198c4cbe50f80ab9a2d3b54fe8b7b6cfb2e9dc5dbbc12c6b',
              'digests': ['public.ecr.aws/supabase/gotrue@sha256:1736a63078f5922b198c4cbe50f80ab9a2d3b54fe8b7b6cfb2e9dc5dbbc12c6b']}
# The releases, in the order they are published, each on top of the one before:
#   good       v0.9.1  the `good` candidate (a newer PostgREST), installed from the console
#   broken     v0.9.2  the `bad` candidate (a PostgREST that never answers), moved back by itself
#   automatic  v0.9.3  good's tree again, installed by automatic mode inside its window
#   attended   v0.9.4  a newer GoTrue (Auth), installed once the operator confirms its warning
RELEASES = {'good': 'v0.9.1', 'broken': 'v0.9.2', 'automatic': 'v0.9.3', 'attended': 'v0.9.4'}
CHANNEL_STAGES = {'good': 'channel-applied', 'broken': 'channel-rolled-back', 'automatic': 'channel-automatic',
                  'attended': 'channel-attended'}
ACKNOWLEDGE = ('This release updates services that change environment databases when they start. Confirm the warning on '
               'the Updates page to install it.')
DEFAULT_SETTINGS = {'check': True, 'automatic': False, 'window': {'start': '03:00', 'end': '05:00'}}
# How long one update may take from the apply to its outcome: the drain, the backup, two starts
# of the runtime and, for the broken release, the 120 second health deadline in between.
FOLLOW_LIMIT = 25 * 60
# Two turns of the supervisor's update scheduling (lab/dev.py UPDATES_EVERY, 15 seconds) and some.
QUIET = 40


def request_json(url, method='GET', body=None, headers=None, timeout=15):
    """(status, parsed body) of one call; (None, reason) when nothing answers. No proxy."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={
        **(headers or {}), **({'content-type': 'application/json'} if data is not None else {})})
    try:
        with opener.open(request, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        with error:
            status, raw = error.code, error.read()
    except (OSError, ValueError) as error:
        return None, error.__class__.__name__
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, None


class Console:
    """The management API as the installation operator, signed in through the management Auth
    realm the way lab/signing-check.py does. The password is read from the private operator file
    and never printed. The address is read again for every call: it may change with a restart."""

    def __init__(self, operator):
        path = Path(operator)
        if path.stat().st_mode & 0o077:
            raise SystemExit('the operator file must be private (mode 600)')
        self.operator = json.loads(path.read_text())
        self.token = None

    def sign_in(self):
        base = console()
        if not base:
            return None
        status, login = request_json(f'{base}/management/auth/v1/token?grant_type=password', 'POST',
                                     {'email': self.operator['email'], 'password': self.operator['password']},
                                     {'apikey': MANAGEMENT_KEY})
        self.token = (login or {}).get('access_token') if status == 200 and isinstance(login, dict) else None
        return status

    def call(self, method, path, body=None):
        for _ in range(2):
            if not self.token and self.sign_in() is None:
                return None, None
            base = console()
            if not base:
                return None, None
            status, value = request_json(f'{base}/management/v1{path}', method, body, {'authorization': f'Bearer {self.token}'})
            if status != 401:
                return status, value
            self.token = None
        return status, value

    def view(self):
        status, value = self.call('GET', '/updates')
        return (value or {}).get('data') if status == 200 and isinstance(value, dict) else None


def wait_for(probe, limit, every=2):
    """probe() until it returns something true, at most `limit` seconds; its last value."""
    until = time.monotonic() + limit
    while True:
        value = probe()
        if value or time.monotonic() >= until:
            return value
        time.sleep(every)


def window_around(offset, moment=None, before=30, after=120):
    """A maintenance window from `before` minutes ago to `after` minutes from now, as clock times in
    the zone whose UTC offset is `offset` (+HH:MM), the one the supervisor reads it in."""
    import datetime
    match = re.fullmatch(r'([+-])(\d{2}):(\d{2})', offset or '')
    if not match:
        raise ValueError(f'not a UTC offset: {offset!r}')
    shift = datetime.timedelta(hours=int(match[2]), minutes=int(match[3])) * (-1 if match[1] == '-' else 1)
    local = (moment or datetime.datetime.now(datetime.UTC)) + shift
    clock = lambda value: value.strftime('%H:%M')
    return {'start': clock(local - datetime.timedelta(minutes=before)), 'end': clock(local + datetime.timedelta(minutes=after))}


def unit(*properties):
    """Properties of sbarbase.service, which any user may read."""
    result = subprocess.run(['systemctl', 'show', 'sbarbase', *(f'--property={name}' for name in properties)],
                            capture_output=True, text=True)
    return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)


def read_upgrades(name):
    try:
        value = json.loads((UPGRADES / name).read_text())
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def update_events():
    """(action, detail) of every update.* row in the control catalog's audit log, oldest first, and
    the kinds in the notification outbox. Both hold versions and triggers only."""
    with contextlib.closing(sqlite3.connect(f'file:{CATALOG}?mode=ro', uri=True, timeout=30)) as database:
        audit = [(action, json.loads(detail)) for action, detail in database.execute(
            "SELECT action, detail FROM audit_events WHERE action LIKE 'update.%' ORDER BY sequence")]
        outbox = [(kind, json.loads(detail)) for kind, detail in database.execute(
            "SELECT kind, detail FROM notification_outbox WHERE kind LIKE 'update.%' ORDER BY at")]
    return audit, outbox


def recorded_event(events, kind, **detail):
    return any(action == kind and all(found.get(name) == value for name, value in detail.items()) for action, found in events)


class Watch:
    """Follows one update from outside the service, ten times a second, in a thread: the request's
    states, the console going away, the upgrade phases toward `target`, and application traffic
    answered 503 by the hold while that version waited for its health checks."""

    def __init__(self, target):
        import threading
        self.target, self.started = target, time.time()
        self.requests, self.phases, self.down, self.held = [], [], None, None
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def since(self):
        return round(time.time() - self.started, 1)

    def run(self):
        last_request, last_phase = None, None
        while not self.stopping.is_set():
            request = read_upgrades('request.json')
            if request:
                seen = (request.get('kind'), request.get('trigger'), request.get('version'), request.get('state'))
                if seen != last_request:
                    last_request = seen
                    self.requests.append({'at': self.since(), 'kind': seen[0], 'trigger': seen[1], 'version': seen[2],
                                          'state': seen[3]})
            state = upgrade_state()
            phase = (state.get('phase'), state.get('to'))
            if phase != last_phase:
                last_phase = phase
                self.phases.append({'at': self.since(), 'phase': phase[0], 'to': str(phase[1])[:12]})
            url = console()
            if self.down is None and any(item['kind'] == 'apply' and item['state'] == 'running' for item in self.requests):
                if not url or fetch(url + '/health')[0] is None:
                    self.down = self.since()
            if self.held is None and state.get('to') == self.target and state.get('phase') == 'applied' and url and held(url):
                self.held = self.since()
            # Faster while the new version waits: the window may last less than a second.
            time.sleep(.03 if state.get('phase') == 'applied' and state.get('to') == self.target else .1)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stopping.set()
        self.thread.join(timeout=30)

    def summary(self):
        return {'requests': self.requests, 'phases': self.phases, 'console_down_at': self.down, 'held_at': self.held}


def channel_source(path):
    """The local release source, recorded beside what `before` observed."""
    record = load()
    source = release_source(Path(path))
    record['channel'] = {'source': str(source), 'minimum': json.loads(git('show', 'HEAD:release.json'))['version'],
                         'releases': {}}
    RECORD.write_text(json.dumps(record))
    print(f'release source: {source}')
    return 0


def release_parts(name, commits, releases, source):
    """(tree, parent, files) of one release: see RELEASES."""
    def tree(commit):
        return git('rev-parse', f'{commit}^{{tree}}', cwd=source)
    if name == 'good':
        return tree(commits['good']), commits['good'], None
    if name == 'broken':
        return tree(commits['bad']), releases['good']['commit'], None
    if name == 'automatic':
        return tree(releases['good']['commit']), releases['broken']['commit'], None
    parent = releases['automatic']['commit']
    lock = json.loads(git('show', f'{parent}:lab/images.lock.json', cwd=source))
    lock['auth'] = NEWER_AUTH
    return tree(parent), parent, {'lab/images.lock.json': json.dumps(lock, indent=2) + '\n'}


def publish_channel(record, name):
    channel = record['channel']
    tag = RELEASES[name]
    tree, parent, files = release_parts(name, record['commits'], channel['releases'], channel['source'])
    commit = publish_release(channel['source'], tag, tree, parent, signer_key(), channel['minimum'], files)
    channel['releases'][name] = {'tag': tag, 'commit': commit}
    print(f'published {tag} ({commit[:12]}) in {channel["source"]}', flush=True)
    return commit


def staged(stage, body):
    """Runs one channel stage with its recorder; an error ends it as a failed check, recorded."""
    record = load()
    check, finish = recorder(stage, record)
    try:
        body(record, check)
    except Exception as error:
        check('the stage ran to its end', False, f'{error.__class__.__name__}: {error}')
    return finish()


def channel_base(operator):
    def body(record, check):
        then, commits, channel = record['observed'], record['commits'], record['channel']
        state = upgrade_state()
        check('the installation moved to the base of the releases with `upgrade.py start --to` and was confirmed',
              state.get('phase') == 'confirmed' and state.get('to') == commits['base'] and state.get('trigger') == 'cli',
              f"{state.get('phase')} to={str(state.get('to'))[:12]} trigger={state.get('trigger')}")
        check('the checkout is on that base', git('rev-parse', 'HEAD') == commits['base'], git('rev-parse', 'HEAD')[:12])
        signers = (ROOT / 'deploy' / 'release-signers').read_text()
        check('its release-signers lists the throwaway release key beside the maintainer key',
              'upgrade-check@example.com namespaces="git"' in signers and signers.count(' namespaces="git" ') >= 2)
        environment = unit('Environment').get('Environment', '')
        check('the service reads its releases from the local source (a systemd drop-in)',
              f"SBARBASE_RELEASE_SOURCE={channel['source']}" in environment.split(), environment[-200:])
        client = Console(operator)
        check('the operator signs in through the management Auth realm', client.sign_in() == 200 and bool(client.token))
        view = wait_for(client.view, 120)
        current = (view or {}).get('current') or {}
        check('the Updates view names the running version and its commit',
              current.get('version') == channel['minimum'] and current.get('commit') == commits['base'],
              f"{current.get('version')} {str(current.get('commit'))[:12]}")
        compare(check, then, observe())
    return staged('channel-base', body)


def offered(client, version, want):
    """The Updates view once it offers `version` and want(view) holds, else the last view."""
    last = {}

    def probe():
        view = client.view() or {}
        last['view'] = view
        release = view.get('available') or {}
        return view if release.get('version') == version and want(view) else None
    return wait_for(probe, 300, 3) or last.get('view') or {}


def ask_check(check, client, version, want):
    status, _ = client.call('POST', '/updates/check')
    check('the console takes "check now"', status == 202, f'status {status}')
    return offered(client, version, want)


def installable(view):
    install = view.get('install') or {}
    return install.get('possible') is True


def quiet_automatic(check, name, started_at):
    """Automatic mode leaves the release on offer alone for two scheduling turns and more."""
    time.sleep(QUIET)
    request, last = read_upgrades('request.json'), read_upgrades('last-request.json') or {}
    automatic = (request or {}).get('trigger') == 'automatic' or (
        last.get('trigger') == 'automatic' and last.get('version') == RELEASES[name][1:])
    state = upgrade_state()
    check(f'automatic mode leaves {RELEASES[name]} alone for {QUIET} seconds, window open',
          not automatic and state.get('started_at') == started_at,
          f"request={request and request.get('kind')} phase={state.get('phase')}")


def follow(target):
    """The upgrade state once the update to `target` has an outcome: confirmed, moved back, failed."""
    def probe():
        state = upgrade_state()
        if state.get('to') == target and state.get('phase') in ('confirmed', 'rolled_back', 'failed', 'rollback_failed'):
            return state
        return None
    state = wait_for(probe, FOLLOW_LIMIT, 3) or upgrade_state()
    # The gateway reads the hold again at most once a second (src/gateway/hold.ts).
    time.sleep(2)
    return state


def moved_checks(check, name, record, watch, baseline, state, trigger):
    """Everything one install through the channel must show once its outcome is in."""
    releases, commits = record['channel']['releases'], record['commits']
    tag, commit = RELEASES[name], releases[name]['commit']
    version = tag[1:]
    # The release that ran before: automatic follows good, since broken moved back to it.
    previous = commits['base'] if name == 'good' else releases[{'attended': 'automatic'}.get(name, 'good')]['commit']
    seen = watch.summary()
    running = next((item['at'] for item in seen['requests'] if item['kind'] == 'apply' and item['state'] == 'running'), None)
    check('the supervisor drained before it moved: the request ran while the console still answered',
          running is not None and seen['console_down_at'] is not None and running <= seen['console_down_at'],
          f"running at {running} s, console gone at {seen['console_down_at']} s")
    now = unit('NRestarts', 'InvocationID', 'MainPID')
    restarts = int(now.get('NRestarts') or 0) - int(baseline.get('NRestarts') or 0)
    wanted = 2 if name == 'broken' else 1
    check(f'systemd started Sbarbase again by itself ({wanted} restart(s) at least), a new invocation',
          restarts >= wanted and now.get('InvocationID') != baseline.get('InvocationID'),
          f"{restarts} restart(s), main pid {baseline.get('MainPID')} -> {now.get('MainPID')}")
    guard = state.get('guard') if isinstance(state.get('guard'), dict) else {}
    snapshots = UPGRADES / 'snapshots'
    fresh = sorted(item.name for item in snapshots.iterdir() if item.stat().st_mtime >= watch.started) if snapshots.is_dir() else []
    attempted = state.get('attempted_at') or ''
    check('the new version started behind the gate: the guard counted its start, a fresh control snapshot was taken',
          guard.get('attempts', 0) >= 1 and bool(attempted) and state.get('snapshot') in fresh and len(fresh) >= 2,
          f"guard {guard.get('phase')} x{guard.get('attempts')}, attempted_at {attempted}, "
          f"{len(fresh)} snapshot(s) since the apply")
    if name != 'broken':
        # The broken release never serves: its runtime start fails before the console starts.
        confirmed = next((item['at'] for item in seen['phases'] if item['phase'] == 'confirmed'
                          and item['to'] == commit[:12]), None)
        check('application traffic was answered 503 while the new version waited for its health checks',
              seen['held_at'] is not None, f"held at {seen['held_at']} s, confirmed at {confirmed} s")
    release = state.get('release') if isinstance(state.get('release'), dict) else {}
    check(f'{tag} was recorded as the release, signed', release.get('version') == version and release.get('tag') == tag
          and release.get('signed') is True, json.dumps(release, sort_keys=True))
    head = git('rev-parse', 'HEAD')
    if name == 'broken':
        check('the broken release did not start and was moved back automatically',
              state.get('phase') == 'rolled_back' and state.get('automatic') is True and state.get('to') == commit
              and state.get('from') == previous and bool(state.get('reason')),
              f"{state.get('phase')} automatic={state.get('automatic')}: {state.get('reason')}")
        check('the checkout is on the last good release again', head == previous, head[:12])
        check('the control state snapshot taken when the broken release started was restored',
              bool(state.get('snapshot')) and state.get('restored') == state.get('snapshot'), state.get('restored'))
    else:
        check(f'the update to {tag} was confirmed, triggered by {trigger}',
              state.get('phase') == 'confirmed' and state.get('to') == commit and state.get('from') == previous
              and state.get('trigger') == trigger, f"{state.get('phase')} trigger={state.get('trigger')}")
        check(f'the checkout is on {tag}', head == commit, head[:12])
    return version, previous


def view_checks(check, name, record, version, trigger):
    client = Console(record['operator'])
    check('the operator signs in again with a fresh token', client.sign_in() == 200 and bool(client.token))
    view = wait_for(client.view, 120) or {}
    last, request = view.get('last') or {}, view.get('request') or {}
    phase = 'rolled_back' if name == 'broken' else 'confirmed'
    running = RELEASES['good'][1:] if name == 'broken' else version
    check(f'the Updates view shows {phase}, the version that runs and the finished request',
          last.get('phase') == phase and last.get('version') == version and (view.get('current') or {}).get('version') == running
          and request.get('kind') == 'apply' and request.get('state') == 'done'
          and (name == 'broken' or last.get('trigger') == trigger),
          f"last {last.get('phase')} {last.get('version')} trigger={last.get('trigger')}, runs "
          f"{(view.get('current') or {}).get('version')}, request {request.get('kind')} {request.get('state')}")


def event_checks(check, name, version, trigger, kind):
    audit, outbox = update_events()
    check(f'update.available was recorded for {version}', recorded_event(audit, 'update.available', version=version, **{'class': kind})
          and recorded_event(outbox, 'update.available', version=version))
    if name == 'broken':
        check(f'update.rolled_back was recorded for {version}', recorded_event(audit, 'update.rolled_back', version=version)
              and recorded_event(outbox, 'update.rolled_back', version=version))
    else:
        check(f'update.applied was recorded for {version} with trigger {trigger}',
              recorded_event(audit, 'update.applied', version=version, trigger=trigger)
              and recorded_event(outbox, 'update.applied', version=version, trigger=trigger))


def pin_checks(check, name, now):
    values = now['environments'].values()
    if name == 'attended':
        check('every Auth container and the management Auth run the newer GoTrue',
              bool(now['environments']) and all(item.get('auth_on_pin') for item in values) and now.get('management_auth_on_pin'),
              now.get('auth_tag'))
    else:
        check('every REST container runs the PostgREST of the release that runs',
              bool(now['environments']) and all(item['rest_on_pin'] for item in values), now['rest_tag'])


def channel_apply(name, operator):
    """One release installed from the console (`good`, `broken`, `attended`) or by automatic mode."""
    stage = CHANNEL_STAGES[name]
    trigger = 'automatic' if name == 'automatic' else 'console'
    kind = 'attended' if name == 'attended' else 'safe'

    if name == 'attended':
        # The pin names GoTrue by its index digest, as NEWER_REST does PostgREST: the guest must be
        # able to pull it, and find it again by that id, or this case cannot run at all.
        pulled = subprocess.run(['docker', 'pull', '-q', NEWER_AUTH['digests'][0]], capture_output=True, text=True)
        found = subprocess.run(['docker', 'image', 'inspect', NEWER_AUTH['id']], capture_output=True, text=True)
        if pulled.returncode or found.returncode:
            record = load()
            record.setdefault('skipped', {})[stage] = (f"{NEWER_AUTH['tag']} could not be pulled or found by its digest "
                                                       f"({(pulled.stderr or found.stderr).strip()[-200:]}).")
            RECORD.write_text(json.dumps(record))
            print(f'skipped: {record["skipped"][stage]}')
            return 0

    def body(record, check):
        record['operator'] = operator
        client = Console(operator)
        version = RELEASES[name][1:]
        started_at = upgrade_state().get('started_at')
        if name == 'automatic':
            view = client.view() or {}
            broken = (view.get('available') or {}).get('version') == RELEASES['broken'][1:]
            check('the release that moved back is still on offer and could be installed by hand',
                  broken and installable(view), f"{(view.get('available') or {}).get('version')} {view.get('install')}")
            window = window_around((view.get('timezone') or {}).get('offset'))
            status, saved = client.call('PUT', '/updates/settings', {'check': True, 'automatic': True, 'window': window})
            check('the operator turns automatic updates on, with a window around the server time now',
                  status == 200 and ((saved or {}).get('data') or {}).get('window') == window,
                  f"status {status}, window {window['start']} to {window['end']} at {(view.get('timezone') or {}).get('offset')}")
            quiet_automatic(check, 'broken', started_at)
        commit = publish_channel(record, name)
        baseline = unit('NRestarts', 'InvocationID', 'MainPID')
        with Watch(commit) as watch:
            if name == 'automatic':
                ask_check(check, client, version, lambda _: True)
            else:
                view = ask_check(check, client, version, lambda view: installable(view) and (
                    (view.get('install') or {}).get('acknowledgement') is (name == 'attended')))
                release = view.get('available') or {}
                changes = ', '.join(f"{row.get('label')}: {row.get('before')} -> {row.get('after')}" for row in release.get('changes') or [])
                check(f"the console offers {RELEASES[name]}, signed, class {kind}, and it can be installed now",
                      release.get('version') == version and release.get('signed') is True and release.get('class') == kind
                      and installable(view), f"class {release.get('class')}, {changes or 'no image changes'}")
                if name == 'attended':
                    quiet_automatic(check, 'attended', started_at)
                    status, answer = client.call('POST', '/updates/apply', {'version': version})
                    check('without the acknowledgement the console refuses to install it, with its sentence',
                          status == 409 and (answer or {}).get('message') == ACKNOWLEDGE, f'status {status}')
                asked = {'version': version, **({'acknowledged': True} if name == 'attended' else {})}
                status, _ = client.call('POST', '/updates/apply', asked)
                check('the console takes the install' + (' with the acknowledgement' if name == 'attended' else ''),
                      status == 202, f'status {status}')
            state = follow(commit)
            if name == 'automatic':
                requests = watch.summary()['requests']
                check('automatic mode requested the release by itself', any(
                    item['trigger'] == 'automatic' and item['version'] == version for item in requests),
                    json.dumps(requests[:3]))
        record.setdefault('watches', {})[name] = watch.summary()
        version, _ = moved_checks(check, name, record, watch, baseline, state, trigger)
        now = observe()
        pin_checks(check, name, now)
        view_checks(check, name, record, version, trigger)
        event_checks(check, name, version, trigger, kind)
        compare(check, record['observed'], now)
        if name == 'attended':
            status, saved = client.call('PUT', '/updates/settings', DEFAULT_SETTINGS)
            check('automatic updates are turned off again', status == 200, f'status {status}')
    return staged(stage, body)


# Lines the supervisor and systemd write for one install, in the journal the shell copies.
JOURNAL = {
    'drain': 'Finishing provisioning and other work before the update.',
    'started': 'Update to a newer Sbarbase release started.',
    'exit 42': re.compile(r'Main process exited, code=exited, status=42\b'),
}


def channel_journal(name, path):
    """The journal of sbarbase.service since just before one stage (the shell copies it here)."""
    if CHANNEL_STAGES[name] in (load().get('skipped') or {}):
        print(f'skipped: {CHANNEL_STAGES[name]} did not run')
        return 0

    def body(record, check):
        text = Path(path).read_text(errors='replace')
        version = RELEASES[name][1:]
        wanted = dict(JOURNAL, moved=f'The checkout moved to Sbarbase {version}. Sbarbase restarts on it now.')
        if name == 'automatic':
            wanted['automatic'] = f'Automatic update to Sbarbase {version} requested.'
        if name == 'broken':
            wanted['way back'] = 'The new version did not start, so the checkout moved back to the previous version.'
        for label, line in wanted.items():
            found = line.search(text) is not None if hasattr(line, 'search') else line in text
            check(f'the journal shows it: {label}', found, line.pattern if hasattr(line, 'search') else line)
    return staged(CHANNEL_STAGES[name], body)


CHANNEL_DESCRIPTIONS = {
    'channel-base': 'The installation moved with `upgrade.py start --to` onto a base commit that lists a throwaway release '
                    'key beside the maintainer key, and a systemd drop-in pointed the service at a local release source.',
    'channel-applied': f"{RELEASES['good']} (PostgREST v14.15 to v14.16), checked for and installed from the console as the "
                       'operator: the drain, exit 42, the restart by systemd, the start guard, the gated start with '
                       'application traffic answered 503 until the confirmation.',
    'channel-rolled-back': f"{RELEASES['broken']}, whose PostgREST never answers, installed from the console the same way: "
                           'its runtime did not start, so it moved back by itself with the control snapshot restored, and '
                           'systemd started the previous release again.',
    'channel-automatic': 'Automatic updates turned on with a window around the server time: the release that moved back was '
                         f"not tried again, and {RELEASES['automatic']}, published afterwards, installed itself after "
                         '"check now" and was confirmed with trigger automatic.',
    'channel-attended': f"{RELEASES['attended']} (Auth, GoTrue v2.196.0 to v2.197.0): automatic mode left it alone, the "
                        'console refused it without the acknowledgement and installed it with it, and the operator signed '
                        'in again afterwards.',
}


def evidence(path, required):
    if not RECORD.exists():
        print('upgrade check: nothing was recorded, so no evidence is written', file=sys.stderr)
        return 1
    record = load()
    rows = record.get('checks', [])
    stages = list(dict.fromkeys(row['stage'] for row in rows))
    # A stage the rehearsal could not run, with its reason (channel_apply), is named, not missing.
    skipped = record.get('skipped') or {}
    missing = [stage for stage in required if stage not in stages and stage not in skipped]
    passed = bool(rows) and all(row['ok'] for row in rows) and not missing
    data = (' Data is compared by row counts, and the control catalog by its schema version and a digest of its '
            'organizations, projects, environments and memberships, not every table.')
    if stages and all(stage in CHANNEL_DESCRIPTIONS for stage in stages):
        document = {
            'check': 'update-channel',
            'scope': 'One installation with its environments in the local rehearsal VM under systemd, not a real server. '
                     'Releases came from a local bare repository named by SBARBASE_RELEASE_SOURCE, signed with a throwaway '
                     'key listed for the rehearsal, not from the canonical repository or with the maintainer key. '
                     + ' '.join(CHANNEL_DESCRIPTIONS[stage] for stage in stages) + data
                     + ''.join(f' Skipped, {stage}: {reason}' for stage, reason in skipped.items())
                     + (f" Not run: {', '.join(missing)}." if missing else ''),
            'skipped': skipped, 'releases': (record.get('channel') or {}).get('releases', {}),
            'watches': record.get('watches', {})}
    else:
        document = {'check': 'upgrade',
                    'scope': 'One installation with its environments on a clean machine, not a real server. '
                             + ' '.join(DESCRIPTIONS.get(stage, stage) for stage in stages) + data}
    Path(path).write_text(json.dumps({
        'check': document.pop('check'), 'recorded': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'passed': passed,
        'count': len(rows), 'stages': stages, 'missing': missing, 'scope': document.pop('scope'), **document,
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
    if command == ['channel-source'] and len(argv) == 2:
        return channel_source(argv[1])
    if command == ['channel-base'] and len(argv) == 2:
        return channel_base(argv[1])
    if command == ['channel-apply'] and len(argv) == 3 and argv[1] in RELEASES:
        return channel_apply(argv[1], argv[2])
    if command == ['channel-journal'] and len(argv) == 3 and argv[1] in RELEASES:
        return channel_journal(argv[1], argv[2])
    if command == ['evidence'] and len(argv) >= 2:
        return evidence(argv[1], argv[2:])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
