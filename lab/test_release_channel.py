"""The release channel: signed tags only, classification from the diff, and no side effects.

Every test uses local temporary repositories as the release source; nothing reaches the
network. Signing uses a throwaway ed25519 key made with ssh-keygen for the test.
"""
import functools
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import release
import release_channel as channel
import upgrade

ROOT = Path(__file__).resolve().parents[1]
# Isolated from the user's git configuration (a global tag.gpgSign would sign every tag).
ISOLATED = {'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0'}
IDENTITY = ('-c', 'user.name=test', '-c', 'user.email=test@example.com')


def git(cwd, *args, check=True):
    result = subprocess.run(['git', *IDENTITY, *args], cwd=cwd, text=True, capture_output=True,
                            env={**os.environ, **ISOLATED})
    if check and result.returncode:
        raise AssertionError(f"git {' '.join(args)}: {result.stderr}")
    return result.stdout.strip()


def keygen(path, comment):
    subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', comment, '-f', str(path)],
                   check=True, capture_output=True)
    return path


@functools.lru_cache(maxsize=None)
def ssh_signing():
    """None when git can sign and verify a tag with an SSH key here; otherwise why not."""
    if not shutil.which('ssh-keygen'):
        return 'ssh-keygen is not installed'
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        try:
            key = keygen(base / 'key', 'probe')
            (base / 'signers').write_text(f'probe namespaces="git" {(base / "key.pub").read_text()}')
            repo = base / 'repo'
            repo.mkdir()
            git(repo, 'init', '-q')
            git(repo, 'commit', '-q', '--allow-empty', '-m', 'probe')
            git(repo, '-c', 'gpg.format=ssh', '-c', f'user.signingkey={key}', 'tag', '-s', 'v0.0.1', '-m', 'probe')
            git(repo, '-c', 'gpg.format=ssh', '-c', f'gpg.ssh.allowedSignersFile={base / "signers"}', 'verify-tag', 'v0.0.1')
        except (AssertionError, subprocess.CalledProcessError) as error:
            return f'git cannot sign or verify a tag with an SSH key here: {error}'
    return None


def pin(tag, digit):
    return {'tag': tag, 'id': 'sha256:' + digit * 64}


def manifest(version, minimum_from='0.1.0', migrations=()):
    return {'version': version, 'minimum_from': minimum_from, 'notes': {'en': f'Version {version}.', 'ar': f'الإصدار {version}.'},
            'migrations': list(migrations)}


class Releases:
    """An upstream repository the maintainer commits and tags in, published to a bare remote."""

    def __init__(self, base):
        self.base = base
        self.maintainer = keygen(base / 'maintainer', 'maintainer')
        self.stranger = keygen(base / 'stranger', 'stranger')
        self.signers = base / 'release-signers'
        self.signers.write_text('# maintainer key\n' + f'maintainer namespaces="git" {(base / "maintainer.pub").read_text()}')
        self.remote = base / 'remote.git'
        git(base, 'init', '-q', '--bare', '-b', 'main', str(self.remote))
        self.upstream = base / 'upstream'
        self.upstream.mkdir()
        git(self.upstream, 'init', '-q', '-b', 'main')
        self.files = {'lab/distro-image.lock.json': pin('postgres:17.6', '1'),
                      'lab/images.lock.json': {'db': pin('postgres:17', '2'), 'auth': pin('gotrue:v1', '3'), 'rest': pin('postgrest:v1', '4')},
                      'lab/storage-image.lock.json': pin('storage:v1', '5'),
                      'package.json': {'name': 'sbarbase', 'version': '0.1.0'},
                      'release.json': manifest('0.1.0'),
                      'Dockerfile': 'FROM ubuntu:26.04\n',
                      'compose.yaml': 'services: {}\n',
                      'bun.lock': 'one\n',
                      'src/code.ts': 'export const version = 1\n'}

    def commit(self, message):
        for name, value in self.files.items():
            path = self.upstream / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if value is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(value if isinstance(value, str) else json.dumps(value, indent=2))
        git(self.upstream, 'add', '-A')
        git(self.upstream, 'commit', '-q', '-m', message)
        return git(self.upstream, 'rev-parse', 'HEAD')

    def release(self, version, key='maintainer', **changes):
        """Commits release.json for a version plus changes, tags it and publishes it."""
        files = {'release.json': manifest(version, **changes.pop('manifest', {})), **changes}
        commit = self.set_and_commit(f'release {version}', files)
        self.tag('v' + version, key)
        return commit

    def set_and_commit(self, message, files):
        self.files.update(files)
        return self.commit(message)

    def tag(self, tag, key='maintainer'):
        if key in ('maintainer', 'stranger'):
            signing = self.maintainer if key == 'maintainer' else self.stranger
            git(self.upstream, '-c', 'gpg.format=ssh', '-c', f'user.signingkey={signing}', 'tag', '-s', tag, '-m', tag)
        elif key == 'unsigned':
            git(self.upstream, 'tag', '-a', tag, '-m', tag)
        else:
            git(self.upstream, 'tag', tag)
        self.publish()

    def publish(self):
        git(self.upstream, 'push', '-q', '--tags', str(self.remote), 'main')


@unittest.skipIf(ssh_signing(), ssh_signing())
class Fixture(unittest.TestCase):
    """A maintainer repository with a signed v0.1.0, and an installation cloned at it."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        base = Path(directory.name)
        environment = patch.dict(os.environ, {**ISOLATED, 'SBARBASE_RELEASE_SOURCE': str(base / 'remote.git')})
        environment.start()
        self.addCleanup(environment.stop)
        self.releases = Releases(base)
        self.first = self.releases.commit('first')
        self.releases.tag('v0.1.0')
        # The installation: a clone of an unrelated origin, at 0.1.0.
        self.checkout = base / 'installation'
        git(base, 'clone', '-q', str(self.releases.remote), str(self.checkout))
        git(self.checkout, 'remote', 'set-url', 'origin', 'https://example.invalid/fork.git')
        self.available = base / 'state' / 'upgrades' / 'available.json'
        for item in (patch.object(channel, 'ROOT', self.checkout), patch.object(channel, 'SIGNERS', self.releases.signers),
                     patch.object(channel, 'AVAILABLE', self.available)):
            item.start()
            self.addCleanup(item.stop)

    def local(self, *args):
        return git(self.checkout, *args)

    def follow(self):
        """Moves the installation's branch to the maintainer's main."""
        self.releases.publish()
        self.local('fetch', '-q', str(self.releases.remote), 'main')
        self.local('merge', '-q', '--ff-only', 'FETCH_HEAD')


class ChannelTests(Fixture):
    def verified(self, tag):
        return channel.verify(channel.fetch_release(tag))

    def test_releases_are_listed_in_semver_order_and_pre_releases_only_on_preview(self):
        for version in ('0.2.0', '0.10.0', '0.9.0'):
            self.releases.release(version)
        self.releases.release('0.11.0-rc.1')
        self.releases.tag('latest', 'lightweight')
        self.releases.tag('v1.0', 'lightweight')
        stable = channel.list_releases()
        self.assertEqual([item['version'] for item in stable], ['0.1.0', '0.2.0', '0.9.0', '0.10.0'])
        self.assertTrue(all(item['annotated'] for item in stable))
        self.assertEqual(stable[0]['commit'], self.first)
        preview = channel.list_releases(channel='preview')
        self.assertEqual(preview[-1]['tag'], 'v0.11.0-rc.1')

    def test_a_tag_signed_by_a_listed_key_is_accepted(self):
        self.releases.release('0.2.0')
        self.assertIsNone(self.verified('v0.2.0'))

    def test_unsigned_wrong_key_and_lightweight_tags_are_refused(self):
        self.releases.release('0.2.0', key='unsigned')
        self.releases.release('0.3.0', key='stranger')
        self.releases.release('0.4.0', key='lightweight')
        self.assertIn('not signed by a key', self.verified('v0.2.0'))
        self.assertIn('not signed by a key', self.verified('v0.3.0'))
        self.assertIn('not an annotated, signed tag', self.verified('v0.4.0'))

    def test_no_key_in_the_signers_file_refuses_every_release(self):
        self.releases.release('0.2.0')
        ref = channel.fetch_release('v0.2.0')
        empty = self.releases.base / 'empty-signers'
        empty.write_text((ROOT / 'deploy' / 'release-signers').read_text())
        self.assertIn('No release signing key', channel.verify(ref, empty))
        self.assertIn('No release signing key', channel.verify(ref, self.releases.base / 'missing'))
        with patch.object(channel.shutil, 'which', return_value=None):
            self.assertIn('ssh-keygen is not installed', channel.verify(ref))

    def test_a_signed_tag_replayed_under_a_newer_name_is_refused(self):
        self.releases.release('0.2.0')
        git(self.releases.upstream, 'push', '-q', str(self.releases.remote), 'refs/tags/v0.2.0:refs/tags/v0.3.0')
        self.assertIn('names itself', self.verified('v0.3.0'))

    def test_release_names_are_checked_before_any_refspec_is_built(self):
        for name in ('main', 'v1.0', 'v1.0.0:refs/heads/main', '../v1.0.0'):
            with self.assertRaises(channel.ReleaseError):
                channel.fetch_release(name)

    def test_the_manifest_is_validated_and_must_match_its_tag(self):
        commit = self.releases.release('0.2.0')
        channel.fetch_release('v0.2.0')
        self.assertEqual(channel.manifest(commit, 'v0.2.0')['version'], '0.2.0')
        with self.assertRaisesRegex(channel.ReleaseError, 'carries'):
            channel.manifest(commit, 'v0.3.0')
        good = manifest('0.2.0')
        self.assertEqual(channel.validate({**good, 'later_field': True})['version'], '0.2.0')
        for broken in ({**good, 'version': '0.2'}, {**good, 'minimum_from': '0.3.0'}, {**good, 'notes': {'en': 'only English'}},
                       {**good, 'notes': {'en': 'x', 'ar': ' '}}, {**good, 'migrations': 'none'}, {**good, 'migrations': ['']},
                       [good]):
            with self.assertRaises(channel.ReleaseError):
                channel.validate(broken)
        missing = self.releases.set_and_commit('no manifest', {'release.json': None})
        broken = self.releases.set_and_commit('broken manifest', {'release.json': '{not json'})
        self.follow()
        with self.assertRaisesRegex(channel.ReleaseError, 'has no'):
            channel.manifest(missing)
        with self.assertRaisesRegex(channel.ReleaseError, 'not JSON'):
            channel.manifest(broken)

    def classify(self, version, **changes):
        commit = self.releases.release(version, **changes)
        channel.fetch_release('v' + version)
        return channel.classify(self.first, commit)

    def test_code_dependency_and_service_pin_changes_are_safe(self):
        images = {**self.releases.files['lab/images.lock.json'], 'rest': pin('postgrest:v2', '8'), 'db': pin('postgres:18', '9')}
        kind, reasons = self.classify('0.2.0', **{'src/code.ts': 'export const version = 2\n', 'bun.lock': 'two\n',
                                                  'lab/images.lock.json': images})
        self.assertEqual((kind, reasons), ('safe', []))

    def test_image_compose_and_unit_changes_need_a_rebuild(self):
        kind, reasons = self.classify('0.2.0', Dockerfile='FROM ubuntu:26.10\n')
        self.assertEqual(kind, 'rebuild')
        self.assertTrue(any('Dockerfile' in reason for reason in reasons))
        for index, path in enumerate(('deploy/container/start.sh', 'compose.yaml', 'deploy/sbarbase.service', '.dockerignore'), 3):
            kind, reasons = self.classify(f'0.{index}.0', **{path: f'changed {index}\n'})
            self.assertEqual(kind, 'rebuild', path)
            self.assertTrue(any(path in reason for reason in reasons), reasons)

    def test_a_database_image_change_or_a_declared_migration_is_manual(self):
        kind, reasons = self.classify('0.2.0', **{'lab/distro-image.lock.json': pin('postgres:18.0', '7'), 'Dockerfile': 'FROM x\n'})
        self.assertEqual(kind, 'manual')
        self.assertTrue(any('migrate-generation' in reason for reason in reasons))
        self.assertTrue(any('Dockerfile' in reason for reason in reasons))
        kind, reasons = self.classify('0.3.0', manifest={'migrations': ['Move avatars to the new bucket layout']})
        self.assertEqual(kind, 'manual')
        self.assertTrue(any('avatars' in reason for reason in reasons))

    def test_the_current_version_comes_from_release_json_then_package_json(self):
        self.assertEqual(channel.current_version(), {'version': '0.1.0', 'commit': self.first})
        commit = self.releases.set_and_commit('no manifest', {'release.json': None, 'package.json': {'version': '0.1.5'}})
        self.follow()
        self.assertEqual(channel.current_version(), {'version': '0.1.5', 'commit': commit})

    def test_check_reports_the_newest_release_and_writes_it(self):
        self.releases.release('0.2.0')
        commit = self.releases.release('0.3.0', **{'lab/images.lock.json': {**self.releases.files['lab/images.lock.json'],
                                                                             'rest': pin('postgrest:v2', '8')}})
        result = channel.check()
        self.assertEqual(set(result), {'current', 'available', 'refusals', 'checked_at'})
        self.assertEqual(result['current'], {'version': '0.1.0', 'commit': self.first})
        self.assertEqual(result['refusals'], [])
        available = result['available']
        self.assertEqual(set(available), {'version', 'tag', 'commit', 'class', 'reasons', 'notes', 'changes', 'signed',
                                          'minimum_from', 'migrations'})
        self.assertEqual((available['version'], available['tag'], available['commit'], available['class'], available['signed']),
                         ('0.3.0', 'v0.3.0', commit, 'safe', True))
        self.assertEqual(available['notes'], {'en': 'Version 0.3.0.', 'ar': 'الإصدار 0.3.0.'})
        self.assertEqual(available['changes'], [{'image': 'images.lock.json:rest', 'from': 'postgrest:v1', 'to': 'postgrest:v2'}])
        path = channel.write_check(result)
        self.assertEqual(path, self.available)
        self.assertEqual(json.loads(path.read_text()), result)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_nothing_newer_and_an_unreachable_source(self):
        self.assertEqual((channel.check()['available'], channel.check()['refusals']), (None, []))
        result = channel.check(where=str(self.releases.base / 'no-such-repository'))
        self.assertIsNone(result['available'])
        self.assertIn('could not be read', result['refusals'][0])

    def test_an_unsigned_release_is_reported_but_refused(self):
        self.releases.release('0.2.0', key='stranger')
        result = channel.check()
        self.assertEqual((result['available']['version'], result['available']['signed']), ('0.2.0', False))
        self.assertIn('not signed by a key', result['refusals'][0])

    def test_a_release_this_version_cannot_reach_offers_the_newest_one_it_can(self):
        self.releases.release('0.2.0')
        self.releases.release('0.3.0', manifest={'minimum_from': '0.2.0'})
        result = channel.check()
        self.assertEqual(result['available']['version'], '0.2.0')
        self.assertIn('v0.3.0 needs at least version 0.2.0', result['refusals'][0])
        self.releases.release('0.4.0', manifest={'minimum_from': '0.3.0'})
        git(self.releases.upstream, 'push', '-q', '--delete', str(self.releases.remote), 'refs/tags/v0.2.0')
        result = channel.check()
        self.assertIsNone(result['available'])
        self.assertEqual(len(result['refusals']), 2)

    def test_a_check_leaves_branches_tags_and_remotes_as_they_were(self):
        def snapshot():
            return (self.local('for-each-ref', 'refs/heads', 'refs/tags', 'refs/remotes'),
                    git(self.checkout, 'config', '--get-regexp', r'^remote\.', check=False), self.local('rev-parse', 'HEAD'),
                    self.local('status', '--porcelain'))
        before = snapshot()
        self.releases.release('0.2.0')
        self.assertEqual(channel.check()['available']['version'], '0.2.0')
        self.assertEqual(snapshot(), before)
        self.assertIn('refs/sbarbase-releases/tags/v0.2.0', self.local('for-each-ref', '--format=%(refname)', 'refs/sbarbase-releases'))
        self.assertFalse((self.checkout / '.git' / 'FETCH_HEAD').exists())

    def test_prepare_refuses_what_start_must_not_apply(self):
        self.releases.release('0.2.0', Dockerfile='FROM ubuntu:26.10\n')
        with self.assertRaisesRegex(channel.ReleaseError, 'needs a rebuild'):
            channel.prepare('v0.2.0')
        self.assertEqual(channel.prepare('v0.2.0', ['rebuild'])['class'], 'rebuild')
        self.releases.release('0.3.0', manifest={'migrations': ['Rewrite every row']})
        with self.assertRaisesRegex(channel.ReleaseError, 'cannot be applied'):
            channel.prepare('v0.3.0', ['rebuild', 'manual'])
        self.releases.release('0.4.0', key='unsigned')
        with self.assertRaisesRegex(channel.ReleaseError, 'not signed'):
            channel.prepare('v0.4.0')
        self.releases.release('0.5.0', manifest={'minimum_from': '0.4.0'})
        with self.assertRaisesRegex(channel.ReleaseError, 'needs at least'):
            channel.prepare('v0.5.0')
        with self.assertRaisesRegex(channel.ReleaseError, 'not newer'):
            channel.prepare('v0.1.0')


class StartReleaseTests(Fixture):
    """`upgrade.py start --release` applies the verified commit and records the release."""

    def start_patches(self):
        state = self.releases.base / 'state'
        self.pulled = []
        # upgrade.main changes into its checkout; come back before the directory goes.
        self.addCleanup(os.chdir, os.getcwd())
        upgrades, upstream = state / 'upgrades', state / 'upstream'
        upstream.mkdir(parents=True)
        # Every path an upgrade touches points into this test's own directory, never the real .lab.
        for item in (patch.object(upgrade, 'ROOT', self.checkout), patch.object(upgrade, 'UPGRADES', upgrades),
                     patch.object(upgrade, 'STATE_FILE', upgrades / 'state.json'), patch.object(upgrade, 'LOCK', upgrades / 'upgrade.lock'),
                     patch.object(upgrade, 'SNAPSHOTS', upgrades / 'snapshots'), patch.object(upgrade, 'HOLD', upgrades / 'hold'),
                     patch.object(upgrade, 'UPSTREAM', upstream), patch.object(upgrade, 'KEY_STORE', state / 'secrets' / 'managed-keys.sqlite'),
                     patch.object(upgrade, 'SUPERVISOR_LOCK', upstream / 'supervisor.lock'),
                     patch.object(upgrade, 'BACKUP_LOCK', upstream / 'backup.lock'),
                     patch.object(upgrade, 'INTENT', state / 'upgrade-intent.json'),
                     patch.object(upgrade, 'pull', side_effect=self.pulled.append), patch.object(upgrade, 'back_up'),
                     patch.object(upgrade, 'install_dependencies')):
            item.start()
            self.addCleanup(item.stop)

    def test_a_safe_release_starts_and_is_recorded(self):
        self.start_patches()
        commit = self.releases.release('0.2.0')
        with redirect_stdout(io.StringIO()):
            self.assertEqual(upgrade.main(['start', '--release', 'v0.2.0']), 0)
        self.assertEqual(self.local('rev-parse', 'HEAD'), commit)
        self.assertEqual(self.pulled, [commit])
        state = upgrade.load_state()
        self.assertEqual((state['phase'], state['to']), ('applied', commit))
        self.assertEqual(state['release'], {'version': '0.2.0', 'tag': 'v0.2.0', 'class': 'safe', 'signed': True})

    def test_a_refused_release_changes_nothing(self):
        self.start_patches()
        self.releases.release('0.2.0', key='stranger')
        output = io.StringIO()
        with redirect_stdout(output), patch('sys.stderr', output):
            self.assertEqual(upgrade.main(['start', '--release', 'v0.2.0']), 1)
        self.assertIn('Nothing was changed', output.getvalue())
        self.assertEqual(self.local('rev-parse', 'HEAD'), self.first)
        self.assertIsNone(upgrade.load_state())
        with redirect_stdout(io.StringIO()), patch('sys.stderr', io.StringIO()), self.assertRaises(SystemExit):
            upgrade.main(['start', '--to', 'origin/main', '--release', 'v0.2.0'])
        with redirect_stdout(io.StringIO()), patch('sys.stderr', io.StringIO()), self.assertRaises(SystemExit):
            upgrade.main(['start', '--release', 'v0.2.0', '--allow-class', 'manual'])

    def test_the_channel_command_prints_and_writes_the_check(self):
        self.start_patches()
        self.releases.release('0.2.0')
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(upgrade.main(['channel', '--json']), 0)
        printed = json.loads(output.getvalue())
        self.assertEqual(printed['available']['version'], '0.2.0')
        self.assertEqual(json.loads(self.available.read_text()), printed)


class VersionTests(unittest.TestCase):
    def test_semver_precedence(self):
        ordered = ['0.9.0', '0.10.0', '1.0.0-alpha', '1.0.0-alpha.1', '1.0.0-alpha.beta', '1.0.0-beta', '1.0.0-beta.2',
                   '1.0.0-beta.11', '1.0.0-rc.1', '1.0.0', '1.0.1', '1.1.0', '2.0.0']
        self.assertEqual(sorted(reversed(ordered), key=channel.key), ordered)
        for invalid in ('01.0.0', 'v1.0.0', '1.0', '1.0.0-', '1.0.0+build', '', None, 1):
            self.assertIsNone(channel.parse(invalid), invalid)
        self.assertEqual(channel.tag_version('v1.2.3'), '1.2.3')
        self.assertIsNone(channel.tag_version('1.2.3'))

    def test_the_repository_manifest_is_valid_and_matches_package_json(self):
        value = channel.validate(json.loads((ROOT / 'release.json').read_text()))
        self.assertEqual(value['version'], json.loads((ROOT / 'package.json').read_text())['version'])
        self.assertEqual(value['migrations'], [])
        for text in (ROOT / 'release.json').read_text(), (ROOT / 'deploy' / 'release-signers').read_text():
            self.assertFalse({'\u2013', '\u2014'} & set(text))
        # Any key the maintainer adds is one allowed signers line for git's namespace.
        for line in (ROOT / 'deploy' / 'release-signers').read_text().splitlines():
            if line.strip() and not line.lstrip().startswith('#'):
                self.assertRegex(line, r'^\S+ namespaces="git" (ssh-ed25519|ecdsa-sha2-nistp256|ssh-rsa|sk-ssh-ed25519@openssh\.com) [A-Za-z0-9+/=]+')


class PrepareTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        base = Path(directory.name)
        for name in ('release.json', 'package.json'):
            shutil.copy(ROOT / name, base / name)
        for item in (patch.object(release, 'RELEASE', base / 'release.json'), patch.object(release, 'PACKAGE', base / 'package.json')):
            item.start()
            self.addCleanup(item.stop)

    def test_prepare_writes_the_manifest_and_only_the_package_version(self):
        before = release.PACKAGE.read_text()
        commands = release.prepare('0.2.0', 'Faster checks.', 'فحوص أسرع.')
        after = release.PACKAGE.read_text()
        self.assertEqual(after, before.replace('"version": "0.1.0"', '"version": "0.2.0"'))
        written = channel.validate(json.loads(release.RELEASE.read_text()))
        self.assertEqual(written, {'version': '0.2.0', 'minimum_from': '0.1.0', 'notes': {'en': 'Faster checks.', 'ar': 'فحوص أسرع.'},
                                   'migrations': []})
        self.assertIn('فحوص أسرع.', release.RELEASE.read_text())
        self.assertTrue(any('tag -s v0.2.0' in line and 'gpg.format=ssh' in line for line in commands))
        self.assertTrue(any(line.startswith('git push ' + channel.CANONICAL) for line in commands))

    def test_prepare_refuses_a_bad_or_older_version_and_bad_notes(self):
        for version in ('0.2', 'v0.2.0', '0.1.0', '0.0.9'):
            with self.assertRaises(release.PrepareError):
                release.prepare(version, 'en', 'ar')
        with self.assertRaises(release.PrepareError):
            release.prepare('0.2.0', 'en', '')
        with self.assertRaises(release.PrepareError):
            release.prepare('0.2.0', 'en', 'ar', minimum_from='0.3.0')
        self.assertEqual(json.loads(release.RELEASE.read_text())['version'], '0.1.0')
        release.prepare('0.2.0', 'en', 'ar', migrations=['Split the users table'])
        self.assertEqual(json.loads(release.RELEASE.read_text())['migrations'], ['Split the users table'])


if __name__ == '__main__':
    unittest.main()
