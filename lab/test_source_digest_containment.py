"""Source evidence hashing is confined to the public verifier image root."""
import hashlib
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location('source_digest_fixture', Path(__file__).resolve().parents[1] / 'deploy/verify/run_checks.py')
CHECKS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKS)


class SourceDigestContainmentTests(unittest.TestCase):
    def test_other_root_refuses_before_enumeration_or_file_reads(self):
        for root in (Path('/checkout'), Path('/app'), Path('/opt/sbarbase/source'), Path('/')):
            with self.subTest(root=root), patch.object(CHECKS, 'ROOT', root), \
                 patch.object(Path, 'rglob') as enumeration, patch.object(Path, 'read_bytes') as read:
                with self.assertRaisesRegex(ValueError, 'public /opt/sbarbase image root'):
                    CHECKS.source_digest()
                enumeration.assert_not_called()
                read.assert_not_called()

    def test_guard_matches_final_verification_image_workdir(self):
        import ast
        import shlex
        dockerfile = Path(__file__).resolve().parents[1] / 'deploy/verify/Dockerfile'
        workdirs = [shlex.split(line)[1] for line in dockerfile.read_text().splitlines()
                    if line.strip().upper().startswith('WORKDIR ')]
        tree = ast.parse(Path(CHECKS.__file__).read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'source_digest')
        guard = function.body[0]
        self.assertIsInstance(guard, ast.If)
        self.assertIsInstance(guard.test, ast.Compare)
        self.assertEqual(guard.test.left.id, 'ROOT')
        self.assertIsInstance(guard.test.ops[0], ast.NotEq)
        fixed_root = guard.test.comparators[0].args[0].value
        self.assertEqual(fixed_root, workdirs[-1])
        self.assertEqual(fixed_root, '/opt/sbarbase')
        self.assertIsInstance(guard.body[0], ast.Raise)

    def test_matching_native_path_without_valid_marker_never_reads_checkout(self):
        markers = [Mock(is_symlink=Mock(return_value=True)),
                   Mock(is_symlink=Mock(return_value=False), is_file=Mock(return_value=False)),
                   Mock(is_symlink=Mock(return_value=False), is_file=Mock(return_value=True), read_text=Mock(return_value='wrong')),
                   Mock(is_symlink=Mock(return_value=False), is_file=Mock(return_value=True), read_text=Mock(side_effect=OSError('denied'))),
                   Mock(is_symlink=Mock(return_value=False), is_file=Mock(return_value=True), read_text=Mock(side_effect=UnicodeError('invalid')))]
        for marker in markers:
            with self.subTest(marker=marker), patch.object(CHECKS, 'ROOT', Path('/opt/sbarbase')), \
                 patch.object(CHECKS, 'PUBLIC_TREE_MARKER', marker), patch.object(Path, 'rglob') as enumeration, \
                 patch.object(Path, 'read_bytes') as read:
                with self.assertRaisesRegex(ValueError, 'verifier image marker'):
                    CHECKS.source_digest()
                enumeration.assert_not_called(); read.assert_not_called()

    def test_distribution_bakes_read_only_marker_outside_checkout(self):
        dockerfile = (Path(__file__).resolve().parents[1] / 'deploy/verify/Dockerfile').read_text()
        marker = str(CHECKS.PUBLIC_TREE_MARKER)
        self.assertEqual(marker, '/usr/local/share/sbarbase-source-verification/public-tree')
        self.assertEqual(CHECKS.PUBLIC_TREE_MARKER_CONTENT, 'public-source-verification-v1\n')
        self.assertIn("printf 'public-source-verification-v1\\n' > " + marker, dockerfile)
        self.assertIn('chmod 0444 ' + marker, dockerfile)
        self.assertLess(dockerfile.index('chmod 0444 ' + marker), dockerfile.index('USER sbarbase'))
        self.assertFalse(CHECKS.PUBLIC_TREE_MARKER.is_relative_to('/opt/sbarbase'))

    def test_public_image_hash_retains_relative_names_bytes_and_exclusions(self):
        paths = [Path('/opt/sbarbase/z.txt'), Path('/opt/sbarbase/node_modules/package.txt'),
                 Path('/opt/sbarbase/a.txt'), Path('/opt/sbarbase/__pycache__/bytecode'), Path('/opt/sbarbase/directory')]
        contents = {Path('/opt/sbarbase/a.txt'): b'alpha', Path('/opt/sbarbase/z.txt'): b'omega'}
        reads = []
        def read(path):
            reads.append(path)
            return contents[path]
        with patch.object(CHECKS, 'ROOT', Path('/opt/sbarbase')), \
             patch.object(CHECKS, 'PUBLIC_TREE_MARKER', Mock(is_symlink=Mock(return_value=False), is_file=Mock(return_value=True), \
                                                          read_text=Mock(return_value='public-source-verification-v1\n'))), \
             patch.object(Path, 'rglob', return_value=paths) as enumeration, \
             patch.object(Path, 'is_file', lambda path: path != Path('/opt/sbarbase/directory')), \
             patch.object(Path, 'read_bytes', read):
            actual = CHECKS.source_digest()
        expected = hashlib.sha256(b'a.txt\0alpha\0z.txt\0omega\0').hexdigest()
        self.assertEqual(actual, expected)
        self.assertEqual(reads, [Path('/opt/sbarbase/a.txt'), Path('/opt/sbarbase/z.txt')])
        enumeration.assert_called_once_with('*')


if __name__ == '__main__': unittest.main()
