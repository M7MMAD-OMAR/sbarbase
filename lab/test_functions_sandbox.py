"""The Edge Functions main service must pass only worker options the pinned runtime reads.

The pinned edge-runtime (lab/functions-image.lock.json, v1.77.4) ignores an option name it does not
know: `netAccessDisabled` and a top level `importMapPath` sat in lab/functions/main/index.ts, did
nothing, and were taken for a network block (docs/evidence/functions-sandbox-probe-2026-10-30). These
checks read the main service's text, with no Docker, and fail on a dead option, on a missing disk quota
and on a permissions object that is incomplete or too wide. What the options do at run time is in
docs/evidence/functions-sandbox-hardening-2026-10-30.
"""
import hashlib
import ipaddress
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / 'lab' / 'functions' / 'main' / 'index.ts'
EVIDENCE = ROOT / 'docs' / 'evidence' / 'functions-sandbox-hardening-2026-10-30'

# The keys UserWorkerCreateOptions deserializes (serde rename_all camelCase) at tag v1.77.4 of
# github.com/supabase/edge-runtime: ext/workers/lib.rs. A key outside it is silently dropped.
USER_WORKER_CREATE_OPTIONS = frozenset({
    'poolKey', 'servicePath', 'envVars', 'noModuleCache', 'noNpm', 'forceCreate', 'allowRemoteModules',
    'customModuleRoot', 'permissions', 'maybeEszip', 'maybeEntrypoint', 'maybeModuleCode', 'memoryLimitMb',
    'lowMemoryMultiplier', 'workerTimeoutMs', 'cpuTimeSoftLimitMs', 'cpuTimeHardLimitMs', 's3FsConfig',
    'tmpFsConfig', 'otelConfig', 'context', 'staticPatterns', 'traceRateLimitOptions',
})
# JsPermissionsOptions in the same file (no rename_all, so snake case).
PERMISSION_KEYS = frozenset({
    'allow_all', 'allow_env', 'deny_env', 'allow_net', 'deny_net', 'allow_ffi', 'deny_ffi', 'allow_read',
    'deny_read', 'allow_run', 'deny_run', 'allow_sys', 'deny_sys', 'allow_write', 'deny_write', 'allow_import',
})
# TmpFsConfig in crates/fs/impl/tmp_fs.rs (camelCase).
TMP_FS_KEYS = frozenset({'base', 'prefix', 'suffix', 'randomLen', 'quota'})
# Names that were passed and are not options of the pinned runtime.
DEAD_OPTIONS = ('netAccessDisabled', 'importMapPath')
# Every allow list a worker needs: a permissions object that leaves one out denies it.
REQUIRED_ALLOW = ('allow_net', 'allow_env', 'allow_read', 'allow_write', 'allow_import')
MAX_TMP_QUOTA = 128 * 1024 * 1024
# The spellings of the metadata address that deny_net is seen to block (the evidence's deny_net_forms).
# Where Docker allocates bridge subnets and so gateway addresses, by default and in typical custom pools.
BRIDGE_RANGES = tuple(ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
METADATA_FORMS = ('169.254.169.254', '[::ffff:169.254.169.254]', '[fd00:ec2::254]')


def source():
    return MAIN.read_text()


def strip_comments(text):
    """The text without // and /* */ comments; string and template literals are kept as they are."""
    out, i, quote = [], 0, None
    while i < len(text):
        c = text[i]
        if quote:
            out.append(c)
            if c == '\\':
                out.append(text[i + 1])
                i += 1
            elif c == quote:
                quote = None
        elif c in '\'"`':
            quote = c
            out.append(c)
        elif text.startswith('//', i):
            while i < len(text) and text[i] != '\n':
                i += 1
            continue
        elif text.startswith('/*', i):
            end = text.index('*/', i + 2) if '*/' in text[i + 2:] else len(text) - 2
            i = end + 2
            out.append(' ')
            continue
        else:
            out.append(c)
        i += 1
    return ''.join(out)


def balanced(text, start):
    """The text inside the braces that open at text[start]; strings are skipped, brackets are counted."""
    assert text[start] == '{', 'not an object literal'
    depth, i, quote = 0, start, None
    while i < len(text):
        c = text[i]
        if quote:
            if c == '\\':
                i += 1
            elif c == quote:
                quote = None
        elif c in '\'"`':
            quote = c
        elif c in '{[(':
            depth += 1
        elif c in '}])':
            depth -= 1
            if depth == 0:
                return text[start + 1:i]
        i += 1
    raise AssertionError('unbalanced braces in the object literal')


def literal_after(text, pattern, what):
    """The body of the object literal that follows the first match of `pattern`, with a clear failure."""
    match = re.search(pattern, text)
    if not match:
        raise AssertionError(f'{what} not found in {MAIN.relative_to(ROOT)}; update this test if it was renamed')
    return balanced(text, match.end() - 1)


def split_top_level(body):
    """Split an object literal body at its top level commas."""
    entries, depth, quote, current = [], 0, None, []
    i = 0
    while i < len(body):
        c = body[i]
        if quote:
            current.append(c)
            if c == '\\':
                current.append(body[i + 1])
                i += 1
            elif c == quote:
                quote = None
        else:
            if c in '\'"`':
                quote = c
            elif c in '{[(':
                depth += 1
            elif c in '}])':
                depth -= 1
            if c == ',' and depth == 0:
                entries.append(''.join(current))
                current = []
                i += 1
                continue
            current.append(c)
        i += 1
    entries.append(''.join(current))
    return [entry.strip() for entry in entries if entry.strip()]


def keys_of(body, what):
    """key -> value text of an object literal; a spread or a computed key is refused, as it hides keys."""
    keys = {}
    for entry in split_top_level(body):
        match = re.match(r'(?:([A-Za-z_$][\w$]*)|[\'"]([^\'"]+)[\'"])\s*(?::\s*(.*))?$', entry, re.S)
        if not match:
            raise AssertionError(f'{what}: cannot read {entry[:40]!r}; spreads and computed keys are not allowed')
        name = match.group(1) or match.group(2)
        keys[name] = (match.group(3) if match.group(3) is not None else name).strip()
    return keys


def worker_options():
    text = strip_comments(source())
    return keys_of(literal_after(text, r'userWorkers\s*\.\s*create\s*\(\s*\{', 'the userWorkers.create({...}) call'),
                   'userWorkers.create options')


def permissions():
    text = strip_comments(source())
    return keys_of(literal_after(text, r'\bconst\s+PERMISSIONS\b[^=;]*=\s*\{', 'the PERMISSIONS object'), 'PERMISSIONS')


def constant(name):
    match = re.search(r'\bconst\s+' + re.escape(name) + r'\b[^=;]*=\s*([^;]+);', strip_comments(source()))
    if not match:
        raise AssertionError(f'constant {name} not found')
    return match.group(1).strip()


def string_list(expression):
    return re.findall(r'[\'"]([^\'"]*)[\'"]', expression)


class WorkerOptionsTest(unittest.TestCase):
    def test_the_scanner_survives_comments_and_reformatting(self):
        text = strip_comments('a // x {\n/* b { */ userWorkers .create (\n{ k: 1, // c }\n s: `${x}` })')
        body = literal_after(text, r'userWorkers\s*\.\s*create\s*\(\s*\{', 'x')
        self.assertEqual(sorted(keys_of(body, 'x')), ['k', 's'])
        with self.assertRaisesRegex(AssertionError, 'not found'):
            literal_after('nothing here', r'userWorkers', 'the call')
        with self.assertRaisesRegex(AssertionError, 'spreads'):
            keys_of('...other, a: 1', 'x')

    def test_the_parser_finds_the_options(self):
        options = worker_options()
        self.assertIn('servicePath', options)
        self.assertIn('envVars', options)
        self.assertGreaterEqual(len(options), 8)

    def test_no_dead_option_names(self):
        text = strip_comments(source())
        for name in DEAD_OPTIONS:
            self.assertNotRegex(text, r'\b' + name + r'\b', f'{name} is not an option of the pinned runtime')

    def test_every_option_is_one_the_pinned_runtime_reads(self):
        unknown = sorted(set(worker_options()) - USER_WORKER_CREATE_OPTIONS)
        self.assertEqual(unknown, [], 'edge-runtime v1.77.4 silently drops these userWorkers.create keys')

    def test_tmp_quota_is_present_and_bounded(self):
        options = worker_options()
        self.assertIn('tmpFsConfig', options, 'without tmpFsConfig /tmp is unbounded')
        tmp_fs = keys_of(balanced(options['tmpFsConfig'], 0), 'tmpFsConfig')
        self.assertEqual(sorted(set(tmp_fs) - TMP_FS_KEYS), [])
        self.assertIn('quota', tmp_fs)
        quota = tmp_fs['quota']
        if re.fullmatch(r'[A-Za-z_]\w*', quota):
            quota = constant(quota)
        self.assertRegex(quota, r'^[\d\s*_]+$', 'the quota must be a plain product of numbers')
        value = 1
        for factor in quota.split('*'):
            value *= int(factor.replace('_', ''))
        self.assertGreater(value, 0)
        self.assertLessEqual(value, MAX_TMP_QUOTA)

    def test_permissions_are_passed_and_complete(self):
        self.assertEqual(worker_options().get('permissions'), 'PERMISSIONS')
        given = permissions()
        for key in REQUIRED_ALLOW:
            self.assertIn(key, given, f'a permissions object without {key} denies it')

    def test_permissions_have_no_key_beyond_the_five_allows_and_deny_net(self):
        # allow_all, allow_run, allow_sys, allow_ffi, allow_hrtime or an unknown key would widen what a function can do.
        extra = sorted(set(permissions()) - set(REQUIRED_ALLOW) - {'deny_net'})
        self.assertEqual(extra, [], 'PERMISSIONS may hold only the five allow lists and deny_net')

    def test_deny_net_lists_the_metadata_address_and_only_literals(self):
        entries = string_list(permissions()['deny_net'])
        for wanted in METADATA_FORMS:
            self.assertIn(wanted, entries)
        for entry in entries:
            self.assertNotIn('/', entry, f'{entry}: CIDR makes worker creation fail on the pinned runtime')
            if ':' in entry:
                self.assertRegex(entry, r'^\[[0-9a-fA-F:.]+\]$', f'{entry}: an IPv6 literal must be bracketed')
            else:
                self.assertRegex(entry, r'^[0-9.]+$', f'{entry} is not a literal address')

    def test_loopback_and_the_gateway_are_not_denied(self):
        # SUPABASE_URL is the main service on loopback; Storage is the listener at the network gateway, an address
        # in the ranges Docker hands out to bridges. No listed entry may fall in either.
        for entry in string_list(permissions()['deny_net']):
            address = ipaddress.ip_address(entry.strip('[]'))
            self.assertFalse(address.is_loopback, f'{entry} would break SUPABASE_URL')
            for network in BRIDGE_RANGES:
                self.assertFalse(address in network, f'{entry} would break Storage at the gateway')

    def test_files_are_limited_to_tmp(self):
        given = permissions()
        for key in ('allow_read', 'allow_write'):
            self.assertEqual(string_list(given[key]), ['/tmp'], f'{key} must be exactly /tmp')

    def test_net_env_and_import_are_open_on_purpose(self):
        # [] means every host and every variable; a non empty list would break npm:, jsr: and outbound calls.
        given = permissions()
        for key in ('allow_net', 'allow_env', 'allow_import'):
            self.assertEqual(string_list(given[key]), [], f'{key}: [] is what the evidence run proved')


class EvidenceTest(unittest.TestCase):
    def test_evidence_records_the_run_and_the_probe_sources(self):
        results = json.loads((EVIDENCE / 'results.json').read_text())
        self.assertTrue(results['scope'].startswith('One host'))
        self.assertIn('sha256:ad45164b7548bf5820ed7be714789c17ac2865edce79a09cfb4c3756c3d365a8', results['scope'])
        self.assertIn('filesystem quota exceeded', json.dumps(results['final_config']['e_sandbox_probe']))
        for name in ('supa', 'npmimp', 'httpsimp', 'envs', 'extfetch', 'rest', 'probe', 'forms'):
            self.assertTrue((EVIDENCE / 'functions' / 'v1' / name / 'index.ts').is_file(), name)

    def test_recorded_hash_is_the_hash_of_the_main_service(self):
        results = json.loads((EVIDENCE / 'results.json').read_text())
        self.assertEqual(results['main_service_sha256_tested'], hashlib.sha256(MAIN.read_bytes()).hexdigest(),
                         'index.ts changed after the evidence run; rerun the probes and update the hash')

    def test_evidence_records_the_deny_net_forms_and_the_per_worker_quota(self):
        results = json.loads((EVIDENCE / 'results.json').read_text())
        forms = results['deny_net_forms']['results']
        for form in METADATA_FORMS:
            self.assertEqual(forms[form], 'BLOCKED NotCapable', form)
        self.assertIn('per worker', results['summary'])
        self.assertEqual(len(results['final_config']['f_tmp_quota_per_worker']), 8)
        self.assertIn('defence in depth', results['findings']['deny_net_is_defence_in_depth_only'] + results['summary'])

    def test_no_long_dashes_in_the_main_service_or_this_test(self):
        for path in (MAIN, Path(__file__)):
            text = path.read_text()
            for dash in (chr(0x2014), chr(0x2013)):
                self.assertNotIn(dash, text, path.name)


if __name__ == '__main__':
    unittest.main()
