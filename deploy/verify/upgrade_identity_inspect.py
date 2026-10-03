"""Actual public Git and native image admission, without updating a checkout."""
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'lab'))
import image_identity
import install_server
from run_checks import source_digest


def main():
    report = {'scope': 'public-upgrade-image-admission', 'source_sha256': source_digest(),
              'commands': [], 'attempted_image_commands': [], 'cases': [], 'passed': False,
              'limitations': ['Fresh public-lock Git history only, no actual upgrade or rollback.',
                              'Native public image inspect only, no pulls or services.',
                              'No backups, private state, checkout mutation, classic store or release acceptance.']}
    environment = {**os.environ, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'}
    with tempfile.TemporaryDirectory(prefix='sbarbase-upgrade-public-') as temporary:
        repository = Path(temporary)

        def native(command):
            result = subprocess.run(command, text=True, capture_output=True, timeout=60, env=environment)
            report['commands'].append({'args': command, 'exit_code': result.returncode,
                                       'stdout': result.stdout, 'stderr': result.stderr})
            if 'warning' in result.stderr.lower():raise RuntimeError('Native warning: ' + result.stderr.strip())
            return result

        def repository_git(*args, check=True):
            result = native(['git', '-C', str(repository), '-c', 'commit.gpgsign=false',
                             '-c', 'core.hooksPath=/dev/null', *args])
            if check and result.returncode:raise RuntimeError('Fixture Git failed: ' + result.stderr.strip())
            return result

        namespace = {}

        def version_git(*args, check=True):
            if args[0] not in ('ls-tree', 'show'):raise RuntimeError('Unexpected historical Git operation')
            result = repository_git(*args, check=False)
            if result.returncode:raise namespace['UpgradeError'](result.stderr.strip())
            return result.stdout.strip()

        def images(*args, **kwargs):
            report['attempted_image_commands'].append(list(args))
            if len(args) != 3 or args[:2] != ('image', 'inspect'):
                raise RuntimeError('Unexpected upgrade image mutation')
            image_identity.immutable(args[2])
            return native(['docker', *args])

        def forbidden_pull(*args, **kwargs):
            raise RuntimeError('Unexpected public image pull')

        def commit(message):
            repository_git('add', '--all', '--', 'lab')
            repository_git('-c', 'user.name=Public fixture', '-c', 'user.email=fixture@example.invalid',
                           'commit', '--quiet', '-m', message)
            return repository_git('rev-parse', 'HEAD').stdout.strip()

        def refused(commit_id, case, fragment):
            before = len(report['attempted_image_commands'])
            try:
                namespace['pins_at'](commit_id)
                namespace['pull'](commit_id)
            except namespace['UpgradeError'] as error:
                if fragment not in str(error):raise RuntimeError('Unexpected production refusal: ' + str(error))
                if len(report['attempted_image_commands']) != before:
                    raise RuntimeError('Historical lock refusal attempted Docker inventory')
                report['cases'].append({'case': case, 'passed': True, 'detail': str(error)})
            else:raise RuntimeError('Invalid historical lock was accepted')

        try:
            required = {'UpgradeError', 'historical_lock_at', 'images_at', 'pins_at', 'pull'}
            tree = ast.parse((ROOT / 'lab/upgrade.py').read_text())
            nodes = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef))
                     and node.name in required]
            if {node.name for node in nodes} != required:raise RuntimeError('Actual upgrade admission definitions missing')
            guard = ast.parse((ROOT / 'lab/upgrade_guard.py').read_text())
            services = [node.value for node in guard.body if isinstance(node, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == 'SERVICES' for target in node.targets)]
            if len(services) != 1:raise RuntimeError('Actual version intent service mapping unavailable')
            namespace.update({'json': json, 'image_identity': image_identity, 'git': version_git,
                              'SERVICES': ast.literal_eval(services[0]),
                              'install_server': SimpleNamespace(LOCKS=install_server.LOCKS, pull_image=forbidden_pull),
                              'lab': SimpleNamespace(docker=images)})
            exec(compile(ast.Module(body=nodes, type_ignores=[]), 'lab/upgrade.py', 'exec'), namespace)
            (repository / 'lab').mkdir()
            for filename in install_server.LOCKS:
                shutil.copyfile(ROOT / 'lab' / filename, repository / 'lab' / filename)
            repository_git('init', '--quiet', '--initial-branch=public-fixture')
            initial = commit('Public locked image fixture')
            admitted = namespace['images_at'](initial)
            pins = namespace['pins_at'](initial)
            logical = {label: digest for label, digest, _reference in admitted}
            expected = {service: logical[filename + ':' + ('default' if key is None else key)]
                        for service, (filename, key) in namespace['SERVICES'].items()}
            if pins != expected:raise RuntimeError('Version intent changed logical registry digests')
            namespace['pull'](initial)
            report['cases'].append({'case': 'native-public-lock-admission', 'passed': True,
                                    'commit': initial, 'images': admitted, 'logical_pins': pins})
            optional = 'studio-image.lock.json'
            if optional not in install_server.LOCKS:raise RuntimeError('Declared optional fixture lock unavailable')
            (repository / 'lab' / optional).unlink()
            absent = commit('Absent optional historical feature')
            older = namespace['images_at'](absent)
            if not older or any(item[0].startswith(optional + ':') for item in older):
                raise RuntimeError('Optional historical absence not respected')
            report['cases'].append({'case': 'native-optional-path-absence', 'passed': True, 'commit': absent})
            (repository / 'lab' / optional).write_text('null\n')
            invalid = commit('Malformed present optional lock')
            refused(invalid, 'native-present-null-refuses-before-inventory', optional)
            (repository / 'lab' / optional).unlink()
            (repository / 'lab' / 'images.lock.json').unlink()
            core_missing = commit('Missing core lock')
            refused(core_missing, 'native-core-absence-refuses-before-inventory', 'images.lock.json')
            refused('d' * 40, 'native-unavailable-commit-refuses-before-inventory', 'tree')
            report['passed'] = True
        except (ValueError, RuntimeError, OSError, subprocess.SubprocessError,
                namespace.get('UpgradeError', RuntimeError)) as error:
            report['error'] = str(error)
        finally:
            Path('/evidence/report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('scope', 'source_sha256', 'cases', 'passed')}, indent=2))
    if 'error' in report:print(report['error'], file=sys.stderr)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
