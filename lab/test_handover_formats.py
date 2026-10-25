"""The format rule of the handover (docs/engineering/HANDOVER.md, Tests, the format rule): the hash of the
declared required keys is the one FORMATS records, and FORMATS is append only against every release that
carries lab/handover.py (local tags only, no network).

Also here: the scan that no code outside lab/runtime_files.py names the files, and the fixtures every checked
writer of that module must accept, byte for byte as the code it replaced wrote them.
"""
import ast
import json
import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import handover
import run as lab
import runtime_files

ROOT = Path(__file__).resolve().parents[1]


def git(root, *args, check=True):
    return subprocess.run(['git', *args], cwd=root, capture_output=True, text=True, check=check).stdout


def release_refs(root):
    """Local release tags reachable from HEAD: the plain v* tags and the sbarbase release refs."""
    tags = [line for line in git(root, 'tag', '--merged', 'HEAD').split() if line.startswith('v')]
    refs = [line.split()[-1] for line in git(root, 'for-each-ref', '--merged', 'HEAD', '--format=%(objectname) %(objecttype) %(refname)',
                                              'refs/sbarbase-releases/tags/').splitlines()]
    return sorted(set(tags) | set(refs))


def history_problems(root, formats, declared):
    """What in the releases that carry lab/handover.py disagrees with the current FORMATS and declaration:
    a list of sentences, empty when the history is respected; raises when the clone cannot be judged."""
    if git(root, 'rev-parse', '--is-shallow-repository').strip() == 'true':
        raise AssertionError('the format history check needs the full history and the release tags')
    problems = []
    for ref in release_refs(root):
        shown = subprocess.run(['git', 'show', f'{ref}:lab/handover.py'], cwd=root, capture_output=True, text=True)
        if shown.returncode:
            continue
        text = shown.stdout
        for number, digest in __import__('re').findall(r"^    (\d+): '([0-9a-f]{64})',$", text, __import__('re').M):
            if formats.get(int(number)) != digest:
                problems.append(f'{ref}: protocol {number} has another hash now')
        if handover.text_protocol(text) == handover.PROTOCOL:
            old = subprocess.run(['git', 'show', f'{ref}:{handover.FORMATS_FILE}'], cwd=root, capture_output=True, text=True)
            if old.returncode or not handover.declares_all(json.loads(old.stdout), declared):
                problems.append(f'{ref}: a key it declares is declared differently now')
    return problems


class FormatHistory(unittest.TestCase):
    def test_the_recorded_hash_is_the_hash_of_the_declared_required_keys(self):
        declared = json.loads((ROOT / handover.FORMATS_FILE).read_text())
        self.assertEqual(handover.FORMATS[handover.PROTOCOL], handover.formats_hash(declared))

    def test_every_protocol_has_one_hash_line_and_the_current_one_is_the_highest(self):
        text = Path(handover.__file__).read_text()
        self.assertEqual(sorted(handover.FORMATS), sorted(int(n) for n in __import__('re').findall(r"^    (\d+): '[0-9a-f]{64}',$", text, __import__('re').M)))
        self.assertEqual(handover.PROTOCOL, max(handover.FORMATS))

    def test_the_releases_that_carry_the_module_agree_with_the_current_formats(self):
        declared = json.loads((ROOT / handover.FORMATS_FILE).read_text())
        # With no release that carries lab/handover.py yet ("no released protocol yet") there is nothing to disagree with.
        self.assertEqual(history_problems(ROOT, handover.FORMATS, declared), [])

    def repository(self, optional_meaning='m', protocol=1, digest=None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        git(root, 'init', '-q')
        git(root, 'config', 'user.email', 't@example.com')
        git(root, 'config', 'user.name', 't')
        (root / 'lab').mkdir()
        (root / 'src/control').mkdir(parents=True)
        declared = json.loads((ROOT / handover.FORMATS_FILE).read_text())
        declared['.lab/upstream/endpoints.json']['optional']['storage']['meaning'] = optional_meaning
        (root / handover.FORMATS_FILE).write_text(json.dumps(declared))
        text = (f"PROTOCOL = {protocol}\nFORMATS = {{\n    {protocol}: '{digest or handover.FORMATS[handover.PROTOCOL]}',\n}}\n")
        (root / 'lab/handover.py').write_text(text)
        git(root, 'add', '-A')
        git(root, 'commit', '-q', '-m', 'release')
        git(root, 'tag', 'v9.9.9')
        return root

    def test_a_release_whose_optional_key_changed_is_caught(self):
        root = self.repository(optional_meaning='an older meaning')
        problems = history_problems(root, handover.FORMATS, json.loads((ROOT / handover.FORMATS_FILE).read_text()))
        self.assertEqual(len(problems), 1)
        self.assertIn('v9.9.9', problems[0])

    def test_a_release_whose_hash_was_edited_in_place_is_caught(self):
        root = self.repository(digest='0' * 64)
        problems = history_problems(root, handover.FORMATS, json.loads((ROOT / handover.FORMATS_FILE).read_text()))
        self.assertTrue(any('another hash' in problem for problem in problems), problems)

    def test_a_release_that_agrees_passes_and_a_later_optional_key_is_allowed(self):
        root = self.repository(optional_meaning=json.loads((ROOT / handover.FORMATS_FILE).read_text())['.lab/upstream/endpoints.json']['optional']['storage']['meaning'])
        declared = json.loads((ROOT / handover.FORMATS_FILE).read_text())
        declared['.lab/upstream/endpoints.json']['optional']['quota'] = {'type': 'number', 'meaning': 'new'}
        self.assertEqual(history_problems(root, handover.FORMATS, declared), [])

    def test_a_required_key_added_after_a_release_is_caught(self):
        root = self.repository(optional_meaning=json.loads((ROOT / handover.FORMATS_FILE).read_text())['.lab/upstream/endpoints.json']['optional']['storage']['meaning'])
        declared = json.loads((ROOT / handover.FORMATS_FILE).read_text())
        declared['.lab/upstream/endpoints.json']['required']['quota'] = {'type': 'number', 'meaning': 'new'}
        self.assertEqual(len(history_problems(root, handover.FORMATS, declared)), 1)

    def test_a_release_of_another_protocol_is_only_held_to_its_own_hash(self):
        root = self.repository(optional_meaning='anything', protocol=0, digest='f' * 64)
        formats = {**handover.FORMATS, 0: 'f' * 64}
        self.assertEqual(history_problems(root, formats, json.loads((ROOT / handover.FORMATS_FILE).read_text())), [])

    def test_a_shallow_clone_cannot_be_judged_and_says_so(self):
        root = self.repository()
        clone = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__('shutil').rmtree(clone, ignore_errors=True))
        git(clone, 'clone', '-q', '--depth', '1', f'file://{root}', 'shallow')
        with self.assertRaisesRegex(AssertionError, 'needs the full history and the release tags'):
            history_problems(clone / 'shallow', handover.FORMATS, {})

    def test_a_release_without_the_module_is_not_a_problem(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        git(root, 'init', '-q')
        git(root, 'config', 'user.email', 't@example.com')
        git(root, 'config', 'user.name', 't')
        (root / 'a').write_text('x')
        git(root, 'add', '-A')
        git(root, 'commit', '-q', '-m', 'old')
        git(root, 'tag', 'v0.0.1')
        self.assertEqual(history_problems(root, handover.FORMATS, {}), [])


LITERAL = re.compile(r'(?<![\w.-])(endpoints|database|studio|management|runtime)\.json(?![\w.-])|write-pauses')
PRIVATE_NAME = re.compile(r'(?<![A-Za-z0-9_])_(ENDPOINTS|DATABASE|STUDIO|MANAGEMENT|PAUSES|RUNTIME_SECRET)(?![A-Za-z0-9_])')
# Where a hit is allowed, each with its reason (the design's list): the rule's own files, the Bun readers (which
# cannot import Python and only read), the native lab's own file, harnesses that write fixtures into a temporary
# folder, and live checks, drills and tests by name.
ALLOWED = (
    r'src/control/handover-formats\.json', r'lab/test_handover_formats\.py', r'lab/runtime_files\.py',
    r'lab/(upstream-server|upstream-app|scheduler|sbarbase|lifecycle-worker|bootstrap|operator-password|ui-fixture|fresh-worker-sdk)\.ts',
    r'lab/(run|provision)\.py',
    r'deploy/verify/(fenced_restore_inspect|backup_workflow_inspect)\.py',
    r'lab/[^/]*-check\.[^/]*', r'lab/[^/]*_check\.py', r'lab/[^/]*-drill\.py', r'lab/test_[^/]*\.py',
)


def scanned():
    """Every text file under lab/, src/ and deploy/ that is tracked or new, as (relative path, text)."""
    listed = subprocess.run(['git', 'ls-files', '--cached', '--others', '--exclude-standard', 'lab', 'src', 'deploy'], cwd=ROOT,
                            capture_output=True, text=True, check=True).stdout.split()
    for name in listed:
        try:
            text = (ROOT / name).read_text()
        except (OSError, UnicodeDecodeError):
            continue
        yield name, text


def allowed(name):
    return any(re.fullmatch(pattern, name) for pattern in ALLOWED)


def structural_problems(name, text):
    """What a Python file does with lab/runtime_files.py that only the module itself may: import a name with a
    leading underscore (`*` included), or reach one through any alias of the module."""
    problems = []
    tree = ast.parse(text)
    bound = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == 'runtime_files':
            for alias in node.names:
                if alias.name.startswith('_') or alias.name == '*':
                    problems.append(f'{name}:{node.lineno} imports {alias.name}')
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == 'runtime_files':
                    bound.add(alias.asname or alias.name)
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            call = node.value
            function = ast.unparse(call.func)
            if function.endswith('import_module') and call.args and isinstance(call.args[0], ast.Constant) and call.args[0].value == 'runtime_files':
                bound.update(target.id for target in node.targets if isinstance(target, ast.Name))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr.startswith('_') and not node.attr.startswith('__'):
            base = node.value
            if (isinstance(base, ast.Name) and base.id in bound) or (isinstance(base, ast.Attribute) and base.attr == 'runtime_files'):
                problems.append(f'{name}:{node.lineno} reaches {node.attr}')
    return problems


class Scan(unittest.TestCase):
    def test_nothing_outside_the_module_names_the_files(self):
        hits = [f'{name}:{number}: {line.strip()[:90]}' for name, text in scanned() if not allowed(name)
                for number, line in enumerate(text.splitlines(), 1) if LITERAL.search(line)]
        self.assertEqual(hits, [], 'Read and write these files through lab/runtime_files.py')

    def test_no_private_name_of_the_module_is_used_outside_it(self):
        hits = [f'{name}: {match.group(0)}' for name, text in scanned() if name not in ('lab/runtime_files.py', 'lab/test_handover_formats.py')
                for match in PRIVATE_NAME.finditer(text)]
        self.assertEqual(hits, [])

    def test_no_python_file_reaches_a_private_name_through_an_import_or_an_alias(self):
        problems = []
        for name, text in scanned():
            if name.endswith('.py') and name not in ('lab/runtime_files.py', 'lab/test_handover_formats.py'):
                problems += structural_problems(name, text)
        self.assertEqual(problems, [])

    ACCESSORS = re.compile(r'runtime_files\.(endpoints_path|studio_path|management_path|pauses_folder|pause_path|runtime_secret_path_for_tool)\(')
    # The callers that hold a path and not a document, each with the reason it cannot use a read_* function.
    PATHS = {
        'lab/install_server.py': 'hands the secret path to git check-ignore',
        'lab/durable_runtime.py': 'hands the secret path to git check-ignore, and keeps it as Runtime.path, which tests point at a temporary file',
        'lab/database_move.py': 'hands the secret path to another program (--inventory-runtime)',
        'lab/host_watch.py': 'a lenient reader whose default path tests replace (published_environments(path=ENDPOINTS))',
        'lab/lifecycle_auth_realm.py': 'private_json checks the file mode and owner before it reads the secret and the management document',
        'lab/studio.py': 'STATE_FILE, which tests point at a temporary file; load_state and save_state go through read_studio and write_studio',
        'lab/write_pause.py': 'folder and record_path, the places its callers and tests list, unlink and create records in',
    }

    def test_a_path_accessor_is_used_outside_the_module_only_where_a_path_is_what_is_needed(self):
        used = {name for name, text in scanned() if name.endswith('.py') and name not in ('lab/runtime_files.py', 'lab/test_handover_formats.py')
                and not name.startswith('lab/test_') and self.ACCESSORS.search(text)}
        self.assertEqual(used, set(self.PATHS))

    def test_the_scan_catches_what_it_is_for(self):
        self.assertTrue(LITERAL.search("path = STATE / 'endpoints.json'"))
        self.assertTrue(LITERAL.search("a = '.secrets/upstream/runtime.json'"))
        self.assertTrue(LITERAL.search("STATE/'write-pauses'"))
        self.assertIsNone(LITERAL.search("kept-runtime.json"))
        self.assertIsNone(LITERAL.search("database-move.json"))
        self.assertIsNone(PRIVATE_NAME.search('STORAGE_DATABASE = 1'))
        self.assertIsNone(PRIVATE_NAME.search('MULTITENANT_DATABASE_URL'))
        self.assertTrue(PRIVATE_NAME.search('_DATABASE'))
        self.assertTrue(PRIVATE_NAME.search("getattr(m, '_ENDPOINTS')"))
        for code, expected in (("from runtime_files import _ENDPOINTS", 1), ("from runtime_files import *", 1),
                               ("import runtime_files as files\nfiles._ENDPOINTS", 1),
                               ("import importlib\nm = importlib.import_module('runtime_files')\nm._STUDIO", 1),
                               ("import runtime_files\nother.runtime_files._DATABASE", 1),
                               ("import runtime_files\nruntime_files.read_endpoints()", 0),
                               ("from runtime_files import read_endpoints", 0)):
            with self.subTest(code=code):
                self.assertEqual(len(structural_problems('x.py', code)), expected)

    def test_the_allowlist_names_files_that_exist_or_patterns_that_match_some(self):
        names = [name for name, _ in scanned()]
        for pattern in ALLOWED:
            self.assertTrue(any(re.fullmatch(pattern, name) for name in names), pattern)

    def test_every_key_the_routing_path_reads_from_an_entry_is_declared(self):
        declared = json.loads((ROOT / handover.FORMATS_FILE).read_text())['.lab/upstream/endpoints.json']
        names = set(declared['required']) | set(declared['optional'])
        text = (ROOT / 'src/gateway/handler.ts').read_text()
        read = set(re.findall(r'route\.(\w+)', text))
        # Set by lab/upstream-app.ts resolve() from the runtime secret and the key store, not read from the file.
        set_by_resolve = {'keys', 'anonymousToken', 'serviceToken', 'enabled', 'realtimeToken', 'realtimeServiceToken'}
        self.assertEqual(read - names - set_by_resolve, set())
        self.assertIn('serviceConcurrency', declared['optional'])
        self.assertEqual(declared['optional']['serviceConcurrency']['type'], 'object')
        # The keys the writers put in an entry (lab/durable_runtime.py) that the old process may read.
        for key in ('storage', 'functions', 'realtime', 'serviceConcurrency'):
            self.assertIn(key, names)

    def test_the_declaration_names_exactly_the_files_the_module_handles(self):
        self.assertEqual({'.lab/upstream/' + runtime_files._ENDPOINTS, '.lab/upstream/' + runtime_files._DATABASE, '.lab/upstream/' + runtime_files._STUDIO,
                          '.lab/upstream/' + runtime_files._MANAGEMENT, '.lab/upstream/' + runtime_files._PAUSES + '/e_*.json',
                          '.secrets/upstream/' + runtime_files._RUNTIME_SECRET},
                         set(json.loads((ROOT / handover.FORMATS_FILE).read_text())))


E = 'e_' + 'a' * 24
F = 'e_' + 'b' * 24
# What the runtime produces for an environment (lab/durable_runtime.py), with every optional key.
ENTRY = {'auth': 'http://172.30.0.3:9999', 'rest': 'http://172.30.0.4:3000',
         'storage': {'url': 'http://172.30.0.5:5000', 'tenantHost': E + '.storage.internal'},
         'realtime': {'url': 'http://172.30.0.6:4000', 'migrated': 'sha256:1'},
         'functions': {'storage': {'host': '172.31.0.1', 'port': 18080, 'subnet': '172.31.0.0/24'}},
         'database': {'user': E + '_developer', 'database': E}, 'cron': {'user': E + '_cron'}, 'serviceConcurrency': {'rest': 8}}
# Keys a later shape of the file may carry: every writer must keep them.
LATER = {'quota': {'rows': 5}, 'zone': 'b'}


class Folders(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name) / 'upstream'
        self.private = Path(directory.name) / 'private'
        self.state.mkdir()
        self.private.mkdir()


class WriterBytes(Folders):
    """Each writer puts on disk what the code it replaced put there: the same JSON through the same atomic write."""

    def old(self, path, value):
        """The code that was in the callers: lab.atomic on the path, into a sibling folder for comparison."""
        target = path.parent / 'expected' / path.name
        target.parent.mkdir(exist_ok=True)
        lab.atomic(target, value)
        return target.read_bytes()

    def test_endpoints_whole_and_patched(self):
        document = {E: ENTRY, F: {'auth': 'http://172.30.1.3:9999', 'rest': 'http://172.30.1.4:3000'}}
        runtime_files.write_endpoints(document, self.state)
        path = runtime_files.endpoints_path(self.state)
        self.assertEqual(path.read_bytes(), self.old(path, document))
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(sorted(entry.name for entry in self.state.iterdir()), ['endpoints.json', 'expected'])
        # The address rewrites of backup.py, rolling.py and write_pause.py.
        expected = json.loads(json.dumps(document))
        expected[E]['rest'] = 'http://172.30.0.9:3000'
        runtime_files.update_endpoints(E, lambda entry: entry.__setitem__('rest', 'http://172.30.0.9:3000'), self.state)
        self.assertEqual(path.read_bytes(), self.old(path, expected))
        expected[E]['functions'] = {'storage': {'host': '172.31.0.2', 'port': 18081, 'subnet': '172.31.0.0/24'}}
        runtime_files.update_endpoints(E, lambda entry: {**entry, 'functions': expected[E]['functions']}, self.state)
        self.assertEqual(path.read_bytes(), self.old(path, expected))

    def test_database_management_studio_runtime_secret(self):
        runtime_files.write_database('172.30.0.2', 5432, self.state)
        self.assertEqual((self.state / 'database.json').read_bytes(), self.old(self.state / 'database.json', {'host': '172.30.0.2', 'port': 5432}))
        runtime_files.update_management('http://172.30.0.7:9999', self.state)
        self.assertEqual(runtime_files.management_path(self.state).read_bytes(),
                         self.old(self.state / 'management.json', {'auth': 'http://172.30.0.7:9999'}))
        studio = {'sessions': {E: {'url': 'http://172.30.0.8:3000', 'password': 'x'}}, 'upstream': {'host': '172.30.0.1', 'port': 25432}}
        runtime_files.write_studio(studio, self.state)
        self.assertEqual(runtime_files.studio_path(self.state).read_bytes(), self.old(self.state / 'studio.json', studio))
        secret = {'admin': 'a' * 64, 'storage_control': 'b' * 64, 'storage_admin': 'c' * 64, 'encryption': 'd' * 64,
                  'environments': {E: {'jwt': 'e' * 64}}, 'management': {'auth': 'f' * 64, 'jwt': '0' * 64}}
        runtime_files.write_runtime_secret(secret, self.private)
        path = runtime_files.runtime_secret_path_for_tool(self.private)
        self.assertEqual(path.read_bytes(), self.old(path, secret))
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(runtime_files.read_runtime_secret(self.private), secret)

    def test_the_runtime_secret_as_it_is_first_written_has_no_management_yet(self):
        first = {'admin': 'a' * 64, 'environments': {}}
        runtime_files.write_runtime_secret(first, self.private)
        self.assertEqual(runtime_files.read_runtime_secret(self.private), first)

    def test_a_pause_record_is_the_one_write_pause_wrote(self):
        record = {'version': 1, 'runtime': E, 'phase': 'held', 'since': 1}
        path = runtime_files.pause_path(E, self.state)
        runtime_files.write_pause(path, record)
        self.assertEqual(path.read_text(), json.dumps(record, indent=2) + '\n')
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.assertEqual(runtime_files.paused_runtimes(self.state), {E})
        self.assertEqual(runtime_files.read_pause(E, self.state), record)
        self.assertTrue(runtime_files.pause_exists(E, self.state))
        self.assertFalse(runtime_files.pause_exists(F, self.state))
        self.assertEqual([entry.name for entry in path.parent.iterdir()], [E + '.json'])

    def test_a_write_pause_module_record_is_unchanged(self):
        import write_pause
        record = {'version': 1, 'runtime': E, 'phase': 'pausing'}
        path = write_pause.record_path(E, self.state)
        write_pause.write_record(path, record)
        self.assertEqual(path.read_text(), json.dumps(record, indent=2) + '\n')
        self.assertEqual(write_pause.folder(self.state), runtime_files.pauses_folder(self.state))
        with self.assertRaises(write_pause.PauseError):
            write_pause.record_path('not-a-runtime', self.state)


class WayBack(Folders):
    """The compatibility rule: a file a later shape wrote is patched by every writer and keeps what it carries."""

    def test_an_endpoints_entry_keeps_every_key_it_was_given(self):
        document = {E: {**ENTRY, **LATER}, F: {'auth': 'a', 'rest': 'r', 'future': [1]}, 'another': {'auth': 'x', 'rest': 'y'}}
        runtime_files.write_endpoints(document, self.state)
        runtime_files.update_endpoints(E, lambda entry: entry.update(rest='http://172.30.0.9:3000'), self.state)
        written = json.loads(runtime_files.endpoints_path(self.state).read_text())
        self.assertEqual(written[E], {**ENTRY, **LATER, 'rest': 'http://172.30.0.9:3000'})
        self.assertEqual(written[F], document[F])
        self.assertEqual(written['another'], document['another'])

    def test_the_management_document_keeps_its_other_keys(self):
        runtime_files.management_path(self.state).write_text(json.dumps({'auth': 'old', **LATER}))
        runtime_files.update_management('http://172.30.0.7:9999', self.state)
        self.assertEqual(json.loads(runtime_files.management_path(self.state).read_text()), {'auth': 'http://172.30.0.7:9999', **LATER})

    def test_the_database_document_keeps_its_other_keys(self):
        (self.state / 'database.json').write_text(json.dumps({'host': 'h', 'port': 1, **LATER}))
        runtime_files.write_database('172.30.0.2', 5432, self.state)
        self.assertEqual(json.loads((self.state / 'database.json').read_text()), {'host': '172.30.0.2', 'port': 5432, **LATER})

    def test_the_studio_state_a_caller_loaded_comes_back_whole(self):
        runtime_files.write_studio({'sessions': {}, **LATER}, self.state)
        loaded = runtime_files.read_studio(self.state)
        loaded['sessions'][E] = {'url': 'u'}
        runtime_files.write_studio(loaded, self.state)
        self.assertEqual(json.loads(runtime_files.studio_path(self.state).read_text()), {'sessions': {E: {'url': 'u'}}, **LATER})

    def test_a_writer_never_rejects_a_key_it_does_not_know(self):
        runtime_files.write_runtime_secret({'environments': {E: {'jwt': 'x', 'next': 1}}, 'management': {'jwt': 'y'}, **LATER}, self.private)
        runtime_files.write_pause(runtime_files.pause_path(E, self.state), {**LATER})


class WriterChecks(Folders):
    def test_each_writer_refuses_a_malformed_value_of_a_key_it_sets_and_writes_nothing(self):
        cases = (
            lambda: runtime_files.write_endpoints([], self.state),
            lambda: runtime_files.write_endpoints({E: 'text'}, self.state),
            lambda: runtime_files.write_endpoints({E: {'auth': 5}}, self.state),
            lambda: runtime_files.write_endpoints({E: {'rest': ['x']}}, self.state),
            lambda: runtime_files.write_endpoints({E: {'storage': 'x'}}, self.state),
            lambda: runtime_files.write_endpoints({E: {'functions': []}}, self.state),
            lambda: runtime_files.write_endpoints({E: {'realtime': 1}}, self.state),
            lambda: runtime_files.write_database(5, 5432, self.state),
            lambda: runtime_files.write_database('h', '5432', self.state),
            lambda: runtime_files.write_database('h', True, self.state),
            lambda: runtime_files.update_management(5, self.state),
            lambda: runtime_files.write_studio([], self.state),
            lambda: runtime_files.write_studio({'sessions': []}, self.state),
            lambda: runtime_files.write_studio({'sessions': {}, 'upstream': 'x'}, self.state),
            lambda: runtime_files.write_runtime_secret([], self.private),
            lambda: runtime_files.write_runtime_secret({'environments': []}, self.private),
            lambda: runtime_files.write_runtime_secret({'management': 'x'}, self.private),
            lambda: runtime_files.write_pause(runtime_files.pause_path(E, self.state), []),
            lambda: runtime_files.pause_path('nope', self.state),
        )
        for index, case in enumerate(cases):
            with self.subTest(index):
                with self.assertRaises(runtime_files.FormatError):
                    case()
        self.assertEqual(list(self.state.iterdir()), [])
        self.assertEqual(list(self.private.iterdir()), [])

    def test_a_patch_of_something_that_is_not_published_or_not_an_object_is_refused(self):
        with self.assertRaises(runtime_files.FormatError):
            runtime_files.update_endpoints(E, lambda entry: None, self.state)
        runtime_files.endpoints_path(self.state).write_text(json.dumps({E: 'text'}))
        with self.assertRaises(runtime_files.FormatError):
            runtime_files.update_endpoints(E, lambda entry: None, self.state)
        for text in ('[]', json.dumps([E])):
            runtime_files.endpoints_path(self.state).write_text(text)
            with self.assertRaises(runtime_files.FormatError):
                runtime_files.update_endpoints(E, lambda entry: None, self.state)

    def test_a_writer_judges_only_what_it_writes_not_an_entry_a_later_shape_wrote(self):
        # F carries realtime as a string: a later PROTOCOL changed that key's type. Publishing an address of E must still work.
        later = {E: {'auth': 'a', 'rest': 'r'}, F: {'auth': 'a', 'rest': 'r', 'realtime': 'on'}}
        runtime_files.endpoints_path(self.state).write_text(json.dumps(later))
        runtime_files.update_endpoints(E, lambda entry: entry.update(rest='r2'), self.state)
        written = json.loads(runtime_files.endpoints_path(self.state).read_text())
        self.assertEqual(written, {E: {'auth': 'a', 'rest': 'r2'}, F: later[F]})
        written[E]['rest'] = 'r3'
        runtime_files.write_endpoints(written, self.state)
        self.assertEqual(json.loads(runtime_files.endpoints_path(self.state).read_text())[F], later[F])
        # What it does write is still judged, whoever else is in the file.
        with self.assertRaises(runtime_files.FormatError):
            runtime_files.update_endpoints(E, lambda entry: entry.update(rest=5), self.state)
        with self.assertRaises(runtime_files.FormatError):
            runtime_files.write_endpoints({**written, E: {'auth': 5}}, self.state)
        # An entry that is new is judged too, and one carried through that is not an object is refused.
        with self.assertRaises(runtime_files.FormatError):
            runtime_files.write_endpoints({**written, 'e_' + 'c' * 24: {'storage': 'x'}}, self.state)
        with self.assertRaises(runtime_files.FormatError):
            runtime_files.write_endpoints({**written, F: 'text'}, self.state)

    def test_a_truncated_or_empty_database_or_management_document_is_written_over_as_before(self):
        for text in ('', '{"host":"1.2.3.4","po', '[1]', 'null'):
            with self.subTest(text=text):
                (self.state / 'database.json').write_text(text)
                runtime_files.write_database('10.0.0.2', 5432, self.state)
                self.assertEqual(json.loads((self.state / 'database.json').read_text()), {'host': '10.0.0.2', 'port': 5432})
                runtime_files.management_path(self.state).write_text(text)
                runtime_files.update_management('http://10.0.0.3:9999', self.state)
                self.assertEqual(json.loads(runtime_files.management_path(self.state).read_text()), {'auth': 'http://10.0.0.3:9999'})

    def test_update_endpoints_has_production_callers(self):
        for name in ('rolling.py', 'write_pause.py'):
            self.assertIn('runtime_files.update_endpoints(', (ROOT / 'lab' / name).read_text())

    def test_a_patch_that_would_leave_a_declared_key_of_the_wrong_type_is_refused_and_the_file_is_untouched(self):
        runtime_files.write_endpoints({E: ENTRY}, self.state)
        before = runtime_files.endpoints_path(self.state).read_bytes()
        with self.assertRaises(runtime_files.FormatError):
            runtime_files.update_endpoints(E, lambda entry: entry.update(rest=5), self.state)
        self.assertEqual(runtime_files.endpoints_path(self.state).read_bytes(), before)

    def test_the_management_writer_argument_replaces_the_atomic_write(self):
        seen = []
        runtime_files.update_management('http://x:1', self.state, writer=lambda path, value: seen.append((path.name, value)))
        self.assertEqual(seen, [('management.json', {'auth': 'http://x:1'})])
        self.assertEqual(list(self.state.iterdir()), [])

    def test_the_writer_argument_replaces_the_atomic_write(self):
        seen = []
        runtime_files.write_endpoints({E: ENTRY}, self.state, writer=lambda path, value: seen.append((path.name, value)))
        self.assertEqual(seen, [('endpoints.json', {E: ENTRY})])
        self.assertEqual(list(self.state.iterdir()), [])


class Readers(Folders):
    def test_endpoints_missing_unreadable_and_malformed(self):
        path = runtime_files.endpoints_path(self.state)
        self.assertEqual(runtime_files.read_endpoints(self.state), {})
        self.assertEqual(runtime_files.read_endpoints(self.state, strict=True), {})
        with self.assertRaises(FileNotFoundError):
            runtime_files.read_endpoints(self.state, required=True)
        path.write_text('{')
        self.assertEqual(runtime_files.read_endpoints(self.state), {})
        with self.assertRaises(ValueError):
            runtime_files.read_endpoints(self.state, strict=True)
        path.write_text('[1]')
        self.assertEqual(runtime_files.read_endpoints(self.state, strict=True), [1])

    def test_read_database_is_strict_about_a_corrupt_file_and_none_for_a_missing_one(self):
        self.assertIsNone(runtime_files.read_database(self.state))
        (self.state / 'database.json').write_text(json.dumps({'host': 'h', 'port': 1}))
        self.assertEqual(runtime_files.read_database(self.state), {'host': 'h', 'port': 1})
        (self.state / 'database.json').write_text('{"host":')
        with self.assertRaises(ValueError):
            runtime_files.read_database(self.state)

    def test_the_other_readers(self):
        self.assertEqual(runtime_files.read_studio(self.state), {'sessions': {}})
        self.assertIsNone(runtime_files.read_management(self.state))
        self.assertFalse(runtime_files.management_present(self.state))
        self.assertIsNone(runtime_files.read_database(self.state))
        self.assertEqual(runtime_files.studio_sessions(self.state), set())
        runtime_files.studio_path(self.state).write_text(json.dumps({'sessions': {E: {}}}))
        self.assertEqual(runtime_files.studio_sessions(self.state), {E})
        runtime_files.studio_path(self.state).write_text('{')
        self.assertEqual(runtime_files.studio_sessions(self.state), set())
        self.assertEqual(runtime_files.paused_runtimes(self.state), set())
        self.assertFalse(runtime_files.runtime_secret_exists(self.private))
        self.assertEqual(runtime_files.runtime_secret_relative(), '.secrets/upstream/runtime.json')

    def test_the_default_folders_are_the_installations(self):
        self.assertEqual(runtime_files.endpoints_path(), ROOT / '.lab/upstream/endpoints.json')
        self.assertEqual(runtime_files.runtime_secret_path_for_tool(), ROOT / '.secrets/upstream/runtime.json')


class CallerSeams(Folders):
    def test_the_start_publishes_the_database_and_management_addresses_through_the_writers(self):
        # Only a live start runs these two lines; the scan keeps any other way of writing the files out of the tree.
        text = (ROOT / 'lab/durable_runtime.py').read_text()
        self.assertIn("runtime_files.write_database(self.endpoint(DB, 5432).split('//')[1].split(':')[0], 5432, STATE)", text)
        self.assertIn('runtime_files.update_management(endpoint, STATE)', text)
        self.assertEqual(text.count('runtime_files.write_runtime_secret(self.values, self.path.parent)'), 6)

    """The modules that moved onto the functions keep behaving as their tests and callers expect."""

    def test_the_runtime_s_published_endpoints_follow_its_state_folder(self):
        import durable_runtime
        with patch.object(durable_runtime, 'STATE', self.state):
            self.assertEqual(durable_runtime.published_endpoints(), {})
            runtime_files.write_endpoints({E: ENTRY}, self.state)
            self.assertEqual(durable_runtime.published_endpoints(), {E: ENTRY})
            self.assertFalse(durable_runtime.studio_running(E))
            runtime_files.write_studio({'sessions': {E: {}}}, self.state)
            self.assertTrue(durable_runtime.studio_running(E))

    def test_database_move_still_refuses_an_unreadable_or_malformed_list(self):
        import database_move
        with patch.object(database_move.runtime, 'STATE', self.state):
            self.assertEqual(database_move.published(), {})
            for text in ('{', '[1]'):
                runtime_files.endpoints_path(self.state).write_text(text)
                with self.assertRaises(database_move.MoveError):
                    database_move.published()

    def test_the_upgrade_health_reads_the_same_files(self):
        import upgrade_health
        runtime_files.write_endpoints({E: ENTRY}, self.state)
        runtime_files.update_management('http://172.30.0.7:9999', self.state)
        runtime_files.write_pause(runtime_files.pause_path(F, self.state), {'version': 1})
        self.assertEqual(upgrade_health.write_paused(self.state), {F})


if __name__ == '__main__':
    unittest.main()
