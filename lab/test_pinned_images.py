"""Pinned image verification: a tag is not an identity, the digest is."""
import unittest
import pinned_images_check as check

PIN='sha256:'+'a'*64


class EvaluateTests(unittest.TestCase):
    def test_a_present_pin_matching_the_repo_digest_passes(self):
        record={'RepoDigests':['postgres@'+PIN],'Id':'sha256:'+'b'*64,'RepoTags':['postgres:17']}
        ok,detail=check.evaluate(PIN,record)
        self.assertTrue(ok)
        self.assertIn('matches the pin',detail)

    def test_a_present_pin_matching_the_local_id_passes(self):
        record={'RepoDigests':[],'Id':PIN,'RepoTags':['sbarbase-db:local']}
        self.assertTrue(check.evaluate(PIN,record)[0])

    def test_a_tag_that_resolves_to_another_digest_fails(self):
        record={'RepoDigests':['postgres@sha256:'+'c'*64],'Id':'sha256:'+'d'*64,'RepoTags':['postgres:17']}
        ok,detail=check.evaluate(PIN,record)
        self.assertFalse(ok)
        self.assertIn('no digest matches',detail)

    def test_a_missing_image_fails_with_a_clear_reason(self):
        ok,detail=check.evaluate(PIN,None)
        self.assertFalse(ok)
        self.assertEqual(detail,'not present locally')

    def test_the_check_never_pulls(self):
        from pathlib import Path
        source=Path(check.__file__).read_text()
        self.assertNotIn("'pull'",source)
        self.assertIn("'image','inspect'",source)

    def test_every_lock_pin_is_covered_by_the_check(self):
        pins=check.install_server.pinned_images()
        self.assertGreaterEqual(len(pins),4)
        for _,digest,reference in pins:
            self.assertTrue(digest.startswith('sha256:'))
            self.assertIn('@sha256:',reference)


if __name__=='__main__':unittest.main()