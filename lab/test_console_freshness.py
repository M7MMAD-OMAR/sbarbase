"""Build freshness: an up-to-date page must not be rebuilt, a stale one must be."""
import os
from pathlib import Path
import tempfile
import time
import unittest
import console_build_check as check

PAGE='<html><head><link rel="stylesheet" href="/assets/app.css"></head><body><script type="module" src="/assets/app.js"></script></body></html>'


class FreshnessTests(unittest.TestCase):
    def setUp(self):
        self.build=tempfile.TemporaryDirectory();self.addCleanup(self.build.cleanup)
        self.sources=tempfile.TemporaryDirectory();self.addCleanup(self.sources.cleanup)
        self.out=Path(self.build.name);(self.out/'assets').mkdir()
        (self.out/'assets'/'app.css').write_text('body{}')
        (self.out/'assets'/'app.js').write_text('console.log(1)')
        (self.out/'index.html').write_text(PAGE)
        self.source=Path(self.sources.name)/'App.tsx';self.source.write_text('export const x=1;')
        # An input written after the build is what makes a build stale; start it older.
        past=time.time()-120
        os.utime(self.source,(past,past))

    def is_fresh(self,roots=None):
        return check.is_fresh(self.out,roots or (str(self.source),))

    def test_a_usable_build_newer_than_its_inputs_is_fresh(self):
        fresh,detail=self.is_fresh()
        self.assertTrue(fresh)
        self.assertIn('newer than every input',detail)

    def test_a_changed_source_makes_the_build_stale(self):
        future=time.time()+30
        os.utime(self.source,(future,future))
        fresh,detail=self.is_fresh()
        self.assertFalse(fresh)
        self.assertIn('newer than the built page',detail)

    def test_a_broken_build_is_never_fresh(self):
        (self.out/'assets'/'app.js').unlink()
        fresh,detail=self.is_fresh()
        self.assertFalse(fresh)
        self.assertIn('not usable',detail)
        self.assertIn('referenced asset missing',detail)

    def test_a_missing_build_is_never_fresh(self):
        empty=Path(tempfile.mkdtemp())
        fresh,detail=check.is_fresh(empty,(str(self.source),))
        self.assertFalse(fresh)
        self.assertIn('index.html is missing',detail)

    def test_the_flutter_of_sources_covers_ui_and_config(self):
        newest=check.newest_source_mtime(('ui','vite.config.ts','package.json'))
        self.assertGreater(newest,0)
        self.assertEqual(check.newest_source_mtime(('does-not-exist.xyz',)),0.0)


if __name__=='__main__':unittest.main()