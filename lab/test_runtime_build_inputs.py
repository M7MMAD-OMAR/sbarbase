"""Controller build-input contract, including adversarial Dockerfile fixtures.

These checks inspect the declared recipe. Actual package resolution, baked files
and platform support require the separate controller image build and inspection.
"""
from pathlib import Path
import re
import shlex
import unittest


ROOT = Path(__file__).resolve().parent.parent
IMAGES = {
    'ubuntu:26.04': '3595d7fc4286a33fad0fd853a4063e654287a9c3787437d7937c94ca3f7a804e',
    'oven/bun:1.3.14': 'e10577f0db68676a7024391c6e5cb4b879ebd17188ab750cf10024a6d700e5c4',
    'docker:29-cli': 'b1805116a6a86cc591b5d5f60a910a0715cdcc9d18d866ad68b1457ead25c35c',
}
SNAPSHOT = '20261002T000000Z'
CA_CHECKSUM = 'f7025ab9b24cd73215510931037b02d6960d89584d0d00afba81851abdbe6ef1'
CA_URL = ('https://snapshot.ubuntu.com/ubuntu/' + SNAPSHOT
          + '/pool/main/c/ca-certificates/ca-certificates_20260223_all.deb')
PACKAGES = {'python3', 'python3-cryptography', 'git', 'openssh-client',
            'tzdata', 'procps', 'ca-certificates', 'util-linux'}


def instructions(text):
    """Join Docker continuations before parsing instruction flags and stages."""
    pending = ''
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if stripped.endswith('\\'):
            pending += stripped[:-1] + ' '
            continue
        joined = pending + stripped
        pending = ''
        instruction, separator, value = joined.partition(' ')
        if not separator:
            raise ValueError('missing instruction arguments')
        yield instruction.upper(), value
    if pending:
        raise ValueError('unfinished continuation')


def flags_and_arguments(value):
    words = shlex.split(value)
    flags = {}
    while words and words[0].startswith('--'):
        option = words.pop(0)[2:]
        key, equal, flag_value = option.partition('=')
        if not equal:
            if not words:
                raise ValueError('missing flag value')
            flag_value = words.pop(0)
        if key in flags:
            raise ValueError('duplicate flag')
        flags[key.lower()] = flag_value
    return flags, words


def external_images(text):
    """Resolve prior stage names/indexes, never treat them as registry images."""
    aliases = set()
    prior_aliases = set()
    stages = 0
    images = []
    for instruction, value in instructions(text):
        flags, words = flags_and_arguments(value) if instruction in {'FROM', 'COPY'} else ({}, [])
        if instruction == 'FROM':
            if len(words) not in {1, 3} or (len(words) == 3 and words[1].upper() != 'AS'):
                raise ValueError('invalid FROM')
            if words[0].lower() not in aliases:
                images.append(words[0])
            prior_aliases = set(aliases)
            if len(words) == 3:
                alias = words[2].lower()
                if alias in aliases or not re.fullmatch(r'[a-z][a-z0-9_.-]*', alias):
                    raise ValueError('invalid or duplicate stage alias')
                aliases.add(alias)
            stages += 1
        elif instruction == 'COPY' and 'from' in flags:
            source = flags['from']
            if source.isdecimal():
                if int(source) >= stages - 1:
                    raise ValueError('COPY references unavailable stage index')
            elif source.lower() in aliases and source.lower() not in prior_aliases:
                raise ValueError('COPY references its own stage')
            elif source.lower() not in prior_aliases:
                images.append(source)
    return images


def shell_commands(value):
    lexer = shlex.shlex(value, posix=True, punctuation_chars=';&|')
    lexer.whitespace_split = True
    commands = [[]]
    for token in lexer:
        if token in {'&&', ';', '|', '||', '&'}:
            if token in {'||', '&'}:
                raise ValueError('build command can mask errors or run asynchronously')
            commands.append([])
        else:
            commands[-1].append(token)
    return commands


def validate_contract(text):
    approved = {name + '@sha256:' + digest for name, digest in IMAGES.items()}
    found = set(external_images(text))
    if found != approved:
        raise ValueError('external image inputs differ from reviewed digest set')
    parsed = list(instructions(text))
    bootstrap = [(flags_and_arguments(value)) for kind, value in parsed if kind == 'ADD']
    if bootstrap != [({'checksum': 'sha256:' + CA_CHECKSUM}, [CA_URL, '/tmp/ca-certificates.deb'])]:
        raise ValueError('missing checksum-pinned CA bootstrap')
    runs = [value for kind, value in parsed if kind == 'RUN']
    expected_snapshot = 'APT::Snapshot "' + SNAPSHOT + '";'
    commands = [command for run in runs for command in shell_commands(run)]
    snapshot_commands = [command for command in commands if command and command[0] == 'printf'
                         and expected_snapshot in ' '.join(command)
                         and '/etc/apt/apt.conf.d/50snapshot' in command]
    if not snapshot_commands:
        raise ValueError('missing frozen APT snapshot configuration')
    ca_config = (expected_snapshot + '\\nAcquire::https::CaInfo '
                 + '"/etc/ssl/certs/ca-certificates.crt";\\n')
    trust_recipe = [
        ['dpkg-deb', '--extract', '/tmp/ca-certificates.deb', '/tmp/ca-bootstrap'],
        ['mkdir', '-p', '/etc/ssl/certs'],
        ['cat', '/tmp/ca-bootstrap/usr/share/ca-certificates/mozilla/*.crt',
         '>', '/etc/ssl/certs/ca-certificates.crt'],
        ['printf', ca_config, '>', '/etc/apt/apt.conf.d/50snapshot'],
    ]
    for command, reason in zip(trust_recipe, [
        'checksum package extraction', 'CA bundle directory',
        'CA bundle construction', 'exact HTTPS CA configuration',
    ]):
        if commands.count(command) != 1:
            raise ValueError('missing or ambiguous ' + reason)
    apt_commands = [command for command in commands if 'apt-get' in command]
    updates = [command for command in apt_commands if 'update' in command]
    if not updates or any(
        [word for word in command if word.startswith('APT::Update::Error-Mode=')]
        != ['APT::Update::Error-Mode=any'] for command in updates
    ):
        raise ValueError('APT partial index errors must fail')
    trust_indexes = [commands.index(command) for command in trust_recipe]
    if trust_indexes != sorted(trust_indexes) or trust_indexes[-1] >= min(
        commands.index(command) for command in updates
    ):
        raise ValueError('HTTPS trust must be constructed before package update')
    installs = [command for command in apt_commands if 'install' in command]
    if not installs or not PACKAGES.issubset(set(word for command in installs for word in command)):
        raise ValueError('required runtime tools missing')
    copies = [flags_and_arguments(value) for kind, value in parsed if kind == 'COPY']
    if ({'chmod': '0755'}, ['deploy/container/start.sh', '/usr/local/bin/sbarbase-start']) not in copies:
        raise ValueError('executable startup missing')
    if ({}, ['lab/docker_profile.py', '/usr/local/lib/sbarbase/docker_profile.py']) not in copies:
        raise ValueError('baked validator missing')
    if ('ENTRYPOINT', '["/usr/local/bin/sbarbase-start"]') not in parsed:
        raise ValueError('startup entrypoint missing')


class RuntimeBuildInputTests(unittest.TestCase):
    def setUp(self):
        self.recipe = (ROOT / 'Dockerfile').read_text()

    def refuse(self, text, reason):
        with self.assertRaisesRegex(ValueError, reason):
            validate_contract(text)

    def test_controller_recipe_meets_input_contract(self):
        validate_contract(self.recipe)

    def test_mutable_base_image_refuses(self):
        self.refuse(self.recipe.replace('@sha256:' + IMAGES['ubuntu:26.04'], ''), 'external image')

    def test_mutable_external_copy_refuses(self):
        self.refuse(self.recipe.replace('@sha256:' + IMAGES['docker:29-cli'], ''), 'external image')

    def test_substituted_digest_refuses(self):
        self.refuse(self.recipe.replace(IMAGES['oven/bun:1.3.14'], '0' * 64), 'external image')

    def test_stage_aliases_and_numeric_copies_are_resolved(self):
        bun = 'oven/bun:1.3.14@sha256:' + IMAGES['oven/bun:1.3.14']
        prefix = 'FROM ' + bun + ' AS builder\nFROM builder AS clone\n'
        recipe = prefix + self.recipe.replace('--from=' + bun, '--from=0')
        validate_contract(recipe)
        validate_contract(recipe.replace('--from=0', '--from=clone'))

    def test_mutable_stage_source_refuses(self):
        self.refuse('FROM oven/bun:1.3.14 AS builder\n' + self.recipe, 'external image')

    def test_unknown_alias_is_external_and_refuses(self):
        self.refuse(self.recipe + '\nCOPY --from=unknown /tool /tool\n', 'external image')

    def test_forward_numeric_stage_refuses(self):
        self.refuse(self.recipe + '\nCOPY --from=9 /tool /tool\n', 'unavailable stage')

    def test_self_referencing_stage_refuses(self):
        self.refuse(self.recipe.replace('\nCOPY --from=oven', ' AS runtime\nCOPY --from=oven', 1)
                    + '\nCOPY --from=runtime /tool /tool\n', 'own stage')

    def test_missing_snapshot_refuses(self):
        self.refuse(self.recipe.replace('APT::Snapshot', 'APT::Other'), 'snapshot configuration')

    def test_mutable_ca_download_refuses(self):
        self.refuse(self.recipe.replace('--checksum=sha256:' + CA_CHECKSUM + ' ', ''), 'CA bootstrap')

    def test_missing_or_substituted_checksum_package_extraction_refuses(self):
        extraction = 'dpkg-deb --extract /tmp/ca-certificates.deb /tmp/ca-bootstrap'
        self.refuse(self.recipe.replace(extraction, 'true'), 'checksum package extraction')
        self.refuse(self.recipe.replace(extraction, extraction.replace('ca-certificates.deb', 'other.deb')),
                    'checksum package extraction')

    def test_missing_or_substituted_ca_bundle_construction_refuses(self):
        bundle = ('cat /tmp/ca-bootstrap/usr/share/ca-certificates/mozilla/*.crt '
                  '> /etc/ssl/certs/ca-certificates.crt')
        self.refuse(self.recipe.replace(bundle, 'true'), 'CA bundle construction')
        self.refuse(self.recipe.replace(bundle, bundle.replace('mozilla', 'other')),
                    'CA bundle construction')

    def test_missing_or_substituted_https_ca_configuration_refuses(self):
        ca_info = 'Acquire::https::CaInfo "/etc/ssl/certs/ca-certificates.crt";'
        self.refuse(self.recipe.replace(ca_info, ''), 'HTTPS CA configuration')
        self.refuse(self.recipe.replace(ca_info, ca_info.replace('ca-certificates.crt', 'other.crt')),
                    'HTTPS CA configuration')

    def test_package_update_before_https_trust_refuses(self):
        self.refuse(self.recipe.replace('RUN dpkg-deb',
                    'RUN apt-get update -o APT::Update::Error-Mode=any && dpkg-deb'),
                    'trust must be constructed before')

    def test_partial_apt_update_errors_refuse(self):
        self.refuse(self.recipe.replace(' -o APT::Update::Error-Mode=any', ''), 'partial index errors')

    def test_one_unguarded_update_among_guarded_updates_refuses(self):
        self.refuse(self.recipe + '\nRUN apt-get update -q\n', 'partial index errors')

    def test_overridden_apt_error_policy_refuses(self):
        self.refuse(self.recipe.replace('APT::Update::Error-Mode=any',
                    'APT::Update::Error-Mode=any -o APT::Update::Error-Mode=persistent'), 'partial index errors')

    def test_masked_update_error_refuses(self):
        self.refuse(self.recipe + '\nRUN apt-get update -o APT::Update::Error-Mode=any || true\n', 'mask errors')

    def test_missing_runtime_tools_or_startup_refuses(self):
        self.refuse(self.recipe.replace('python3-cryptography ', ''), 'runtime tools')
        self.refuse(self.recipe.replace('--chmod=0755 ', ''), 'executable startup')
        self.refuse(self.recipe.replace('COPY lab/docker_profile.py', 'COPY lab/other.py'), 'validator')


if __name__ == '__main__':
    unittest.main()
