"""Console build verification: a broken page is a failure, not a build success."""
from pathlib import Path
import sys
import tempfile
import unittest
import console_build_check as check


def built(files,index='<html><head><link rel="stylesheet" href="/assets/app.css"></head><body><script type="module" src="/assets/app.js"></script></body></html>'):
    directory=tempfile.TemporaryDirectory()
    root=Path(directory.name)
    (root/'assets').mkdir()
    for name,content in files.items():(root/name).write_text(content)
    (root/'index.html').write_text(index)
    return directory,root


class VerifyTests(unittest.TestCase):
    def test_a_complete_build_passes_and_assets_are_hashed(self):
        directory,root=built({'assets/app.css':'body{}','assets/app.js':'console.log(1)'})
        with directory:
            problems,page=check.verify(root)
        self.assertEqual(problems,[])
        self.assertEqual(page['referenced'],2)
        self.assertEqual(sorted(page['assets']),['assets/app.css','assets/app.js'])
        self.assertTrue(all(len(value['sha256'])==64 for value in page['assets'].values()))

    def test_a_missing_index_is_a_problem(self):
        directory,root=built({'assets/app.css':'body{}'})
        with directory:
            (root/'index.html').unlink()
            problems,_=check.verify(root)
        self.assertIn('index.html is missing from the build output',problems)

    def test_an_empty_page_is_a_problem(self):
        directory,root=built({'assets/app.css':'body{}'})
        with directory:
            (root/'index.html').write_text('   \n')
            problems,_=check.verify(root)
        self.assertIn('index.html is empty',problems)

    def test_a_page_pointing_at_an_absent_asset_is_a_problem(self):
        directory,root=built({'assets/app.css':'body{}'})
        with directory:
            problems,_=check.verify(root)
        self.assertIn('referenced asset missing: assets/app.js',problems)

    def test_a_page_with_no_local_asset_is_a_problem(self):
        directory,root=built({},index='<html><body><a href="https://example.com">x</a></body></html>')
        with directory:
            problems,_=check.verify(root)
        self.assertIn('index.html references no local asset',problems)

    def test_remote_and_data_urls_are_not_treated_as_local_assets(self):
        page='<link href="https://cdn.example.com/a.css"><img src="data:image/png;base64,AAA"><script src="/assets/app.js"></script>'
        self.assertEqual(check.asset_references(page),['assets/app.js'])

    def test_build_records_exit_code_and_duration(self):
        record=check.build(('true',))
        self.assertEqual(record['exit'],0)
        self.assertIsInstance(record['seconds'],float)

    def test_a_failing_build_command_is_recorded_not_hidden(self):
        record=check.build((sys.executable,'-c','raise SystemExit(3)'))
        self.assertEqual(record['exit'],3)


if __name__=='__main__':unittest.main()