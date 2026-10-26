#!/usr/bin/env python3
"""The live upgrade measurement (checkpoint cp9): the driver of .github/workflows/live-upgrade-measure.yml.

It runs on a GitHub Actions runner after the workflow installed Sbarbase v0.9.0 in Docker (the repository's
CI helpers: deploy/ci-docker.sh, the compose project sbarbase-ci-*, the console on port 8790, SBARBASE_GUARDED=1
from deploy/container/start.sh) and bootstrapped an operator. Nothing here may run anywhere else: it
changes the checkout, signs releases with a throwaway key and restarts the installation.

  live_upgrade_measure.py run --out DIR --private DIR [--stages a,b,...]    the whole experiment
  live_upgrade_measure.py kill-console                                      inside the container: see below

`run` does, in order (a stage that cannot run is recorded as blocked, with the reason, and the rest go on):

  setup     one environment with a tiny table through the management API (lab/live-upgrade-setup.ts), and the
            pulse client (lab/pulse.py, every 0.2 s, an authenticated environment read and the anonymous
            /health) for the whole run
  steady    45 seconds with nothing happening: the noise floor of the runner
  base      the installation moves onto a base commit (v0.9.0 plus the throwaway release key) with
            `upgrade.py start --to` and an ordinary `docker compose restart`: a stop, not the planned upgrade
            exit, recorded for what an ordinary restart costs
  handover  a code only release (one doc line) installed from the console, handover on (the default)
  kill      the same, and the serving console process is killed with SIGKILL once the candidate exists
  rest      a PostgREST pin change installed from the console, with the default rolling (rest)
  reconfigure  the container is recreated with SBARBASE_HANDOVER=0 and SBARBASE_ROLLING_SERVICES=none
  baseline  a code only release with the handover off
  rest-none a PostgREST pin change with rolling off
  window    `upgrade.py database --window max_connections`

Releases are annotated tags signed with the throwaway key, served from a bare repository over HTTP on
127.0.0.1 (deploy/host-preflight.sh accepts a release source only as an http or https URL), and found and
installed by the console exactly as an operator does it. The commits live only in that repository inside the
runner. Every number comes from the pulse; this file records what ran and what the logs said.
"""
import argparse
import importlib.util
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import live_upgrade_summary  # noqa: E402
import pulse  # noqa: E402

TOOLS = Path(__file__).resolve().parent
BASE_URL = 'http://127.0.0.1:8790'
SOURCE_URL = 'http://127.0.0.1:8099'
CONTAINER_MEASURE = '.lab/measure'
STAGES = ('setup', 'steady', 'base', 'handover', 'kill', 'rest', 'reconfigure', 'baseline', 'rest-none', 'window')
DOC = 'docs/engineering/upgrades-known-limits.md'
TAIL_SECONDS = 20
FOLLOW_SECONDS = 20 * 60
ROLL_SECONDS = 10 * 60
FINAL_PHASES = ('confirmed', 'rolled_back', 'failed', 'rollback_failed')


class StageError(Exception):
    pass


class Abort(StageError):
    """A stage that was already recorded and without which the rest cannot run."""


# Pure helpers (unit tested without Docker).

def scrub(text):
    """Removes what must not reach a public log or artifact: tokens and keys, whatever their source."""
    text = re.sub(r'eyJ[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*', '[token]', text)
    text = re.sub(r'sb_(?:publishable|secret)_[A-Za-z0-9_]+', '[key]', text)
    return re.sub(r'(?i)(password|secret)(["\']?\s*[:=]\s*["\']?)[^\s"\',}]+', r'\1\2[hidden]', text)


def classify_handover(logs, before, after, phase=None):
    """What the supervisor did at the end of the upgrade: {result, reason}.

    `before` and `after` are (restart count, container start time) of the sbarbase container. Result is
    'handover' (the supervisor replaced itself in place and the container never restarted), 'refused' (the
    handover was considered and not used; the reason is the condition's own text), 'restarted' (the planned
    exit 42 and a container restart) or 'unknown'."""
    refused = re.search(r'The handover is not used \((?P<why>[^)]*)\)', logs)
    broken = re.search(r'The handover is not used: (?P<why>[^\n]*)', logs) or re.search(r'The handover could not start \((?P<why>[^\n]*)\)', logs)
    failed = re.search(r'The handover failed at (?P<why>[^\n]*)', logs)
    stopped = 'Sbarbase moved to another version and stopped' in logs
    moved = after != before
    if refused:
        return {'result': 'refused', 'reason': refused.group('why'), 'container_restarted': moved}
    if broken:
        return {'result': 'refused', 'reason': broken.group('why'), 'container_restarted': moved}
    if failed:
        return {'result': 'failed', 'reason': failed.group('why'), 'container_restarted': moved}
    if stopped or moved:
        return {'result': 'restarted', 'reason': 'the planned exit 42 and a container restart', 'container_restarted': True}
    if phase == 'confirmed':
        return {'result': 'handover', 'reason': None, 'container_restarted': False}
    return {'result': 'unknown', 'reason': None, 'container_restarted': moved}


def doc_release(text, tag):
    """The one doc line a code only release adds: a comment at the end of a markdown file."""
    return text.rstrip('\n') + f'\n\n<!-- live upgrade measure: {tag} -->\n'


def last_json(output):
    """The last line of an output that is a JSON object, else None."""
    for line in reversed(output.strip().splitlines()):
        line = line.strip()
        if line.startswith('{'):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None


# The shell: the repository's CI helpers are bash functions (BASH_ENV sources deploy/ci-docker.sh).

def sh(script, timeout=900, check=True, stdin=None):
    try:
        result = subprocess.run(['bash', '-c', script], capture_output=True, text=True, timeout=timeout, input=stdin)
    except subprocess.TimeoutExpired as error:
        raise StageError(f'timed out after {timeout} s: {script[:160]}') from error
    if check and result.returncode:
        raise StageError(f'exit {result.returncode}: {script[:160]}\n{scrub((result.stderr or result.stdout)[-1500:])}')
    return result


class Run:
    def __init__(self, out, private, root):
        self.out, self.private, self.root = Path(out), Path(private), Path(root)
        self.out.mkdir(parents=True, exist_ok=True)
        self.private.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.pulse_file = self.out / 'pulse.jsonl'
        self.env_file = self.private / 'environment.json'
        self.operator = self.root / '.secrets' / 'operator.json'
        self.scenarios = []
        self.facts = {}
        self.pulse = None
        self.server = None
        self.extra = {'SBARBASE_RELEASE_SOURCE': SOURCE_URL}
        self.releases = {}
        self.chk = None

    # Container.

    def exec_in(self, *argv, timeout=900, check=True):
        return sh(f'ci_exec sbarbase {shlex.join(argv)}', timeout=timeout, check=check)

    def compose(self, action, timeout=1500):
        assignments = ' '.join(f'{key}={shlex.quote(value)}' for key, value in self.extra.items())
        return sh(f'ci_env env {assignments} "$SBARBASE_ROOT/deploy/compose.sh" {action}', timeout=timeout)

    def container(self):
        """(restart count, start time, id) of the sbarbase container."""
        text = sh("ci_docker inspect -f '{{.RestartCount}}|{{.State.StartedAt}}|{{.Id}}' \"$(ci_compose ps -aq sbarbase)\"").stdout.strip()
        count, started, ident = text.split('|')
        return (int(count), started), ident

    def logs_since(self, moment):
        _, ident = self.container()
        result = sh(f'ci_docker logs --timestamps --since {int(moment)} {ident} 2>&1', check=False, timeout=120)
        return scrub(result.stdout)

    def state(self):
        result = sh(f'sudo cat {shlex.quote(str(self.root / ".lab/upgrades/state.json"))}', check=False, timeout=30)
        try:
            return json.loads(result.stdout)
        except ValueError:
            return {}

    def rest_containers(self):
        """[(name, image id)] of the PostgREST containers, found by the images the pins name."""
        ids = {pin['id'] for pin in self.rest_pins().values()}
        text = sh("ci_docker inspect -f '{{.Name}}|{{.Config.Image}}|{{.Image}}' $(ci_docker ps -aq)", check=False, timeout=60).stdout
        found = []
        for line in text.splitlines():
            name, config, image = (line.split('|') + ['', '', ''])[:3]
            if image in ids or 'postgrest' in config:
                found.append((name.lstrip('/'), image))
        return found

    def rest_pins(self):
        return self.releases.get('pins', {})

    def mark(self, name):
        pulse.mark(self.pulse_file, name)

    def ready(self, limit=300, need=15):
        """True once `need` consecutive environment reads and health reads are answered correctly."""
        api, key, table = pulse.load_target(self.env_file)
        deadline, good = time.monotonic() + limit, 0
        while time.monotonic() < deadline:
            env = pulse.probe('env', BASE_URL, f'{api}/rest/v1/{table}?select=*&limit=1', {'apikey': key}, 3)
            health = pulse.probe('health', BASE_URL, '/health', {}, 3)
            good = good + 1 if env['verdict'] == health['verdict'] == 'ok' else 0
            if good >= need:
                return True
            time.sleep(0.5)
        return False

    # Releases.

    def load_check(self):
        if self.chk is None:
            spec = importlib.util.spec_from_file_location('upgrade_check', self.root / 'lab' / 'upgrade-check.py')
            if spec is None or spec.loader is None:
                raise StageError('lab/upgrade-check.py cannot be loaded')
            self.chk = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.chk)
        return self.chk

    def git(self, *args, cwd=None, env=None, stdin=None):
        return self.load_check().git(*args, cwd=cwd, env=env, stdin=stdin)

    def prepare_base(self):
        """Lists a throwaway release key beside the maintainer key and commits that as `base`, then goes back
        to the installed commit so the installation runs on exactly what v0.9.0 is until it is moved."""
        chk = self.load_check()
        installed = self.git('rev-parse', 'HEAD')
        self.git('config', 'user.email', 'ci@example.com')
        self.git('config', 'user.name', 'ci')
        chk.list_signer()
        self.git('add', '-A')
        self.git('commit', '-q', '--allow-empty', '-m', 'Live upgrade measure: v0.9.0 with the throwaway release key')
        base = self.git('rev-parse', 'HEAD')
        self.git('checkout', '-q', '--detach', installed)
        lock = json.loads(self.git('show', f'{base}:lab/images.lock.json'))
        self.releases.update({'installed': installed, 'base': base, 'last': base, 'tags': {},
                              'pins': {'original': lock['rest'], 'newer': chk.NEWER_REST}})
        self.source = self.root / '.git' / 'measure' / 'releases.git'
        self.source.parent.mkdir(exist_ok=True)
        chk.release_source(self.source)
        self.serve()
        return base

    def serve(self):
        if self.server is None:
            self.git('update-server-info', cwd=self.source)
            self.server = subprocess.Popen(['python3', '-m', 'http.server', '8099', '--bind', '127.0.0.1', '--directory', str(self.source)],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1)

    def publish(self, tag, kind):
        """Publishes one release tag on top of the last one: 'code' adds a doc line, 'rest-newer' and
        'rest-original' move the PostgREST pin. Returns the release commit."""
        chk = self.load_check()
        parent = self.releases['last']
        objects = self.git('rev-parse', '--path-format=absolute', '--git-path', 'objects')
        (self.source / 'objects' / 'info').mkdir(parents=True, exist_ok=True)
        (self.source / 'objects' / 'info' / 'alternates').write_text(objects + '\n')
        if kind == 'code':
            text = self.git('show', f'{parent}:{DOC}', cwd=self.source)
            files = {DOC: doc_release(text, tag)}
        else:
            lock = json.loads(self.git('show', f'{parent}:lab/images.lock.json', cwd=self.source))
            lock['rest'] = self.releases['pins']['newer' if kind == 'rest-newer' else 'original']
            files = {'lab/images.lock.json': json.dumps(lock, indent=2) + '\n'}
        tree = self.git('rev-parse', f'{parent}^{{tree}}', cwd=self.source)
        minimum = json.loads(self.git('show', f'{self.releases["base"]}:release.json'))['version']
        commit = chk.publish_release(self.source, tag, tree, parent, chk.signer_key(), minimum, files)
        # Dumb HTTP cannot follow a local alternate: pack everything the tags reach into the repository itself.
        self.git('repack', '-a', '-d', '-q', cwd=self.source)
        (self.source / 'objects' / 'info' / 'alternates').unlink(missing_ok=True)
        self.git('update-server-info', cwd=self.source)
        self.git('cat-file', '-e', f'{self.releases["base"]}^{{commit}}', cwd=self.source)
        listed = sh(f'git ls-remote --tags {SOURCE_URL} refs/tags/{tag}', check=False, timeout=60).stdout
        if tag not in listed:
            raise StageError(f'{tag} is not served at {SOURCE_URL}: {listed!r}')
        self.releases['last'] = commit
        self.releases['tags'][tag] = commit
        return commit

    # Scenarios.

    def record(self, entry):
        self.scenarios.append(entry)
        (self.out / 'scenarios.json').write_text(json.dumps(self.scenarios, indent=1))
        print(f"scenario {entry['name']}: {entry.get('status')}", flush=True)

    def follow(self, commit, limit=FOLLOW_SECONDS):
        """The upgrade state once the move to `commit` has an outcome."""
        deadline = time.monotonic() + limit
        state = {}
        while time.monotonic() < deadline:
            state = self.state()
            if state.get('to') == commit and state.get('phase') in FINAL_PHASES:
                return state
            time.sleep(2)
        return state

    def wait_roll(self, target_id, limit=ROLL_SECONDS):
        """Seconds until every PostgREST container runs the target image and no `-next` container is left."""
        begun = time.monotonic()
        while time.monotonic() - begun < limit:
            found = self.rest_containers()
            if found and all(image == target_id for _, image in found) and not any(name.endswith('-next') for name, _ in found):
                return round(time.monotonic() - begun, 1), found
            time.sleep(2)
        return None, self.rest_containers()

    def upgrade_scenario(self, name, tag, kind, note, kill=False):
        entry = {'name': name, 'note': note, 'tag': tag, 'kind': kind}
        try:
            commit = self.publish(tag, kind)
            entry['commit'] = commit
            before, _ = self.container()
            entry['state_before'] = {key: self.state().get(key) for key in ('phase', 'to', 'from')}
            watcher = None
            if kill:
                watcher = subprocess.Popen(['bash', '-c', f'ci_exec sbarbase python3 {CONTAINER_MEASURE}/live_upgrade_measure.py kill-console'],
                                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            self.mark(f'{name}:begin')
            asked = time.time()
            answer = last_json(self.exec_in('bun', f'{CONTAINER_MEASURE}/live-upgrade-console.ts', str(self.operator), 'update', tag[1:], timeout=600).stdout)
            if not answer or not answer.get('applied'):
                raise StageError(f'the console did not take the install: {answer}')
            start = answer['at']
            entry['console'] = answer
            self.mark(f'{name}:applied')
            state = self.follow(commit)
            entry['outcome'] = {key: state.get(key) for key in ('phase', 'trigger', 'moved', 'automatic', 'failure', 'started_at', 'finished_at')}
            confirmed = time.time()
            entry['seconds_to_outcome'] = round(confirmed - start, 1)
            if kind.startswith('rest') and state.get('phase') == 'confirmed':
                target = self.releases['pins']['newer' if kind == 'rest-newer' else 'original']
                seconds, found = self.wait_roll(target['id'])
                entry['roll'] = {'target': target['tag'], 'seconds_after_outcome': seconds, 'containers': [list(row) for row in found]}
            self.ready(limit=240, need=10)
            time.sleep(TAIL_SECONDS)
            end = time.time()
            self.mark(f'{name}:end')
            after, _ = self.container()
            logs = self.logs_since(asked - 5)
            entry['handover'] = classify_handover(logs, before, after, state.get('phase'))
            (self.out / 'logs').mkdir(exist_ok=True)
            (self.out / 'logs' / f'{name}.log').write_text('\n'.join(logs.splitlines()[-600:]) + '\n')
            entry['log_lines'] = [line for line in logs.splitlines() if re.search(r'(?i)handover|moved to another|update to a newer|checkout moved|confirm|moved back|rolling|roll\b', line)][:60]
            if watcher is not None:
                try:
                    killed, _ = watcher.communicate(timeout=60)
                except subprocess.TimeoutExpired:
                    watcher.kill()
                    killed = 'the watcher did not end'
                entry['kill'] = scrub((killed or '').strip()[-800:])
            entry['start'], entry['end'] = start, end
            entry['status'] = 'ran'
        except StageError as error:
            entry.update({'status': 'blocked', 'reason': str(error)})
            if 'start' not in entry:
                entry['start'] = entry['end'] = time.time()
        self.record(entry)
        return entry

    def timed(self, name, note, body):
        entry = {'name': name, 'note': note, 'start': time.time()}
        try:
            self.mark(f'{name}:begin')
            entry.update(body() or {})
            self.ready(limit=240, need=10)
            time.sleep(TAIL_SECONDS)
            entry['status'] = 'ran'
        except StageError as error:
            entry.update({'status': 'blocked', 'reason': str(error)})
        entry['end'] = time.time()
        self.mark(f'{name}:end')
        self.record(entry)
        return entry

    # Stages.

    def stage_setup(self):
        sh(f'sudo mkdir -p {shlex.quote(str(self.root / CONTAINER_MEASURE))} && '
           f'cd {shlex.quote(str(TOOLS))} && sudo cp live-upgrade-setup.ts live-upgrade-console.ts live_upgrade_measure.py '
           f'live_upgrade_summary.py pulse.py {shlex.quote(str(self.root / CONTAINER_MEASURE))}/')
        target = f'{CONTAINER_MEASURE}/environment.json'
        self.exec_in('bun', f'{CONTAINER_MEASURE}/live-upgrade-setup.ts', str(self.operator), target, timeout=1200)
        content = sh(f'sudo cat {shlex.quote(str(self.root / target))}').stdout
        self.env_file.write_text(content)
        self.env_file.chmod(0o600)
        sh(f'sudo rm -f {shlex.quote(str(self.root / target))}')
        if not self.ready(limit=120, need=10):
            raise StageError('the environment does not answer correctly through the gateway')
        self.pulse = subprocess.Popen(['python3', str(TOOLS / 'pulse.py'), 'run', '--base', BASE_URL, '--env-file', str(self.env_file),
                                       '--out', str(self.pulse_file)], stderr=open(self.out / 'pulse.stderr', 'w'))
        time.sleep(3)
        self.record({'name': 'setup', 'status': 'ran', 'start': time.time(), 'end': time.time(),
                     'note': 'one environment, one table, the pulse client started'})

    def stage_steady(self):
        self.timed('steady', 'nothing happens: the noise floor of the runner', lambda: time.sleep(45))

    def stage_base(self):
        def body():
            base = self.prepare_base()
            self.exec_in('python3', 'lab/upgrade.py', 'start', '--to', base, timeout=900)
            sh('ci_route restart', timeout=600)
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                state = self.state()
                if state.get('to') == base and state.get('phase') in FINAL_PHASES:
                    break
                time.sleep(3)
            if self.state().get('phase') != 'confirmed':
                raise StageError(f'the move onto the base ended as {self.state().get("phase")}')
            return {'base': base, 'installed': self.releases['installed'],
                    'note': 'an ordinary `docker compose restart` (a stop of everything, not the planned exit 42) after `upgrade.py start --to`'}
        entry = self.timed('base', 'v0.9.0 to the base commit with the CLI and a plain restart', body)
        if entry['status'] != 'ran':
            raise Abort('the base move did not finish: ' + entry.get('reason', ''))

    def stage_handover(self):
        self.upgrade_scenario('handover', 'v0.9.1', 'code', 'a code only release from the console, SBARBASE_HANDOVER at its default (on)')

    def stage_kill(self):
        self.upgrade_scenario('kill', 'v0.9.2', 'code', 'a code only release from the console with the handover on, and the serving console '
                              'process killed with SIGKILL once the candidate exists', kill=True)

    def stage_rest(self):
        self.upgrade_scenario('rest', 'v0.9.3', 'rest-newer', 'a PostgREST pin change from the console, rolling at its default (rest)')

    def stage_reconfigure(self):
        def body():
            self.extra.update({'SBARBASE_HANDOVER': '0', 'SBARBASE_ROLLING_SERVICES': 'none'})
            self.compose('up', timeout=1500)
            return {'env': {key: value for key, value in self.extra.items() if key != 'SBARBASE_RELEASE_SOURCE'}}
        self.timed('reconfigure', 'the container recreated with SBARBASE_HANDOVER=0 and SBARBASE_ROLLING_SERVICES=none', body)

    def stage_baseline(self):
        self.upgrade_scenario('baseline', 'v0.9.4', 'code', 'the same kind of release as `handover` with SBARBASE_HANDOVER=0')

    def stage_rest_none(self):
        self.upgrade_scenario('rest-none', 'v0.9.5', 'rest-original', 'a PostgREST pin change with SBARBASE_ROLLING_SERVICES=none')

    def stage_window(self):
        def body():
            started = time.time()
            result = self.exec_in('python3', 'lab/upgrade.py', 'database', '--window', 'max_connections', timeout=400, check=False)
            return {'command_seconds': round(time.time() - started, 1), 'exit': result.returncode,
                    'output': scrub((result.stdout + result.stderr).strip()[-2500:])}
        entry = self.timed('window', '`upgrade.py database --window max_connections`', body)
        if entry.get('exit') not in (0, None):
            entry['status'] = 'ran, the command refused or failed'

    def finish(self, facts):
        if self.pulse is not None:
            self.pulse.send_signal(signal.SIGTERM)
            try:
                self.pulse.wait(30)
            except subprocess.TimeoutExpired:
                self.pulse.kill()
        if self.server is not None:
            self.server.terminate()
        readings, marks = live_upgrade_summary.load_pulse(self.pulse_file) if self.pulse_file.exists() else ([], [])
        document = live_upgrade_summary.summarize(readings, self.scenarios, {**facts, 'releases': self.releases.get('tags', {}),
                                                                             'base_commit': self.releases.get('base'),
                                                                             'installed_commit': self.releases.get('installed')})
        document['marks'] = [{'t': mark['t'], 'name': mark['name']} for mark in marks]
        (self.out / 'live-upgrade-measure.json').write_text(json.dumps(document, indent=1, ensure_ascii=False) + '\n')


def runner_facts():
    def read(command):
        return subprocess.run(command, shell=True, capture_output=True, text=True).stdout.strip()
    keys = ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'GITHUB_SHA', 'GITHUB_REF', 'GITHUB_REPOSITORY', 'RUNNER_OS', 'RUNNER_ARCH', 'ImageOS', 'ImageVersion')
    return {'runner': {**{key: os.environ.get(key) for key in keys},
                       'cpus': read('nproc'), 'memory': read("free -m | awk 'NR==2 {print $2\" MiB total, \"$7\" MiB available\"}'"),
                       'kernel': read('uname -r'), 'os': read("sed -n 's/^PRETTY_NAME=//p' /etc/os-release"),
                       'docker': read('docker --version'), 'python': read('/usr/bin/python3 --version'),
                       'disk': read("df -h / | awk 'NR==2 {print $2\" total, \"$4\" free\"}'")},
            'started': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}


def kill_console(timeout=900, poll=0.005):
    """Inside the container: waits for the handover's candidate record, then kills the serving console process.

    .lab/upstream/handover.json names the old process (written before the exec) and
    .lab/upstream/candidate.json appears when the new version's console process exists. SIGKILL is sent
    once, with no chance to drain. Prints one JSON line; exit 3 when no handover began (none to disturb)."""
    upstream = Path('.lab/upstream')
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if (upstream / 'candidate.json').exists() and (upstream / 'handover.json').exists():
            try:
                pid = json.loads((upstream / 'handover.json').read_text())['pid']
                candidate = json.loads((upstream / 'candidate.json').read_text()).get('pid')
                os.kill(pid, signal.SIGKILL)
            except (OSError, ValueError, KeyError) as error:
                print(json.dumps({'killed': False, 'error': f'{error.__class__.__name__}: {error}'}))
                return 4
            print(json.dumps({'killed': True, 'target': 'the serving console process', 'pid': pid, 'candidate': candidate, 't': time.time()}))
            return 0
        time.sleep(poll)
    print(json.dumps({'killed': False, 'error': 'no handover began'}))
    return 3


def main(argv=None):
    parser = argparse.ArgumentParser(description=(__doc__ or '').split('\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)
    running = sub.add_parser('run')
    running.add_argument('--out', required=True)
    running.add_argument('--private', required=True)
    running.add_argument('--root', default=os.environ.get('GITHUB_WORKSPACE') or os.getcwd())
    running.add_argument('--stages', default='all')
    sub.add_parser('kill-console')
    args = parser.parse_args(argv)
    if args.command == 'kill-console':
        return kill_console()
    chosen = STAGES if args.stages == 'all' else tuple(item for item in args.stages.split(',') if item)
    unknown = [item for item in chosen if item not in STAGES]
    if unknown:
        raise SystemExit(f'unknown stages: {unknown}')
    run = Run(args.out, args.private, args.root)
    facts = runner_facts()
    aborted = None
    for stage in STAGES:
        if stage not in chosen:
            run.record({'name': stage, 'status': 'skipped', 'reason': 'not selected', 'start': None, 'end': None})
            continue
        if aborted:
            run.record({'name': stage, 'status': 'blocked', 'reason': f'an earlier stage stopped the run: {aborted}', 'start': None, 'end': None})
            continue
        try:
            getattr(run, 'stage_' + stage.replace('-', '_'))()
        except Abort as error:
            aborted = f'{stage}: {str(error)[:200]}'
        except StageError as error:
            run.record({'name': stage, 'status': 'blocked', 'reason': str(error), 'start': None, 'end': None})
            if stage in ('setup', 'base', 'reconfigure'):
                aborted = f'{stage}: {str(error)[:200]}'
        except Exception as error:  # a bug here must not lose the readings so far
            run.record({'name': stage, 'status': 'blocked', 'reason': f'{error.__class__.__name__}: {error}', 'start': None, 'end': None})
            aborted = f'{stage}: {error.__class__.__name__}'
    facts['finished'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    run.finish(facts)
    return 0


if __name__ == '__main__':
    sys.exit(main())
