import unittest
from pathlib import Path

import durable_runtime as runtime

RUNTIME = 'e_' + 'a' * 24


class FunctionDigestMountTests(unittest.TestCase):
    """The digest key and the secret update times (src/control/functions-read.ts) sit beside the folder that is
    mounted into the Edge Functions container, never inside it: code in that container must not read the key."""

    def digest_files(self):
        # digestFolder(secretsRoot) is `<parent of the functions secrets folder>/function-digests`.
        folder = runtime.PRIVATE / 'function-digests'
        return [folder / (RUNTIME + '.key'), folder / (RUNTIME + '.json')]

    def test_no_bind_source_contains_a_digest_file(self):
        for source, _target in runtime.functions_binds(RUNTIME):
            for path in self.digest_files():
                self.assertNotIn(Path(source).resolve(), [path.resolve(), *path.resolve().parents])

    def test_the_secrets_folder_is_where_the_console_writes_it(self):
        private = runtime.functions_private(RUNTIME)
        self.assertEqual(private, runtime.PRIVATE / 'functions' / RUNTIME)
        self.assertIn((private, '/run/sbarbase'), runtime.functions_binds(RUNTIME))
        for path in self.digest_files():
            self.assertNotIn(private.resolve(), [path.resolve(), *path.resolve().parents])


if __name__ == '__main__':
    unittest.main()
