"""The distribution probe follows lab/distro-image.lock.json and never rewrites it."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import image_identity

spec = importlib.util.spec_from_file_location('distro_check', Path(__file__).with_name('distro-check.py'))
distro = importlib.util.module_from_spec(spec)
spec.loader.exec_module(distro)

REPOSITORY = 'public.ecr.aws/supabase/postgres'
INDEX = 'sha256:' + 'a' * 64
CONFIG = 'sha256:' + 'b' * 64


class PinnedImageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / 'lab').mkdir()
        self.lock = self.root / 'lab' / 'distro-image.lock.json'
        self.pin = {'tag': REPOSITORY + ':17.11.0.004', 'id': INDEX, 'digests': [REPOSITORY + '@' + INDEX]}
        self.lock.write_text(json.dumps(self.pin, indent=2))
        self.calls = []
        patcher = patch.object(distro.lab, 'ROOT', self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def daemon(self, digests):
        def docker(*args, **_):
            self.calls.append(args)
            return SimpleNamespace(returncode=0, stdout=json.dumps([{'Id': CONFIG, 'RepoDigests': digests}]))
        return patch.object(distro.lab, 'docker', side_effect=docker)

    def test_the_probe_inspects_the_pinned_digest_and_leaves_the_lock_untouched(self):
        before = self.lock.read_bytes()
        with self.daemon([REPOSITORY + '@' + INDEX]):
            pin, info = distro.pinned_image()
        self.assertEqual(pin, self.pin)
        self.assertEqual(info['Id'], CONFIG)
        self.assertEqual(self.calls, [('image', 'inspect', REPOSITORY + '@' + INDEX)])
        self.assertEqual(self.lock.read_bytes(), before)

    def test_an_image_that_does_not_prove_the_pinned_digest_is_refused(self):
        with self.daemon([REPOSITORY + '@sha256:' + 'c' * 64]):
            with self.assertRaises(image_identity.IdentityError):
                distro.pinned_image()

    def test_a_lock_without_its_digest_proof_is_refused_before_the_daemon_is_asked(self):
        self.lock.write_text(json.dumps({'tag': self.pin['tag'], 'id': INDEX}))
        with self.daemon([REPOSITORY + '@' + INDEX]):
            with self.assertRaises(image_identity.IdentityError):
                distro.pinned_image()
        self.assertEqual(self.calls, [])

    def test_no_code_path_writes_the_distribution_lock(self):
        source = Path(distro.__file__).read_text()
        self.assertNotIn('17.6.1.166', source)
        self.assertNotIn("LOCK).write", source)
        self.assertNotIn("lock.json').write", source)


if __name__ == '__main__':
    unittest.main()
