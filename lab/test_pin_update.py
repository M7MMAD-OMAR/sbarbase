"""Pin update policy enforcement: one component, pinned digest, review entry first."""
import json
from pathlib import Path
from unittest.mock import patch
import tempfile
import unittest
import pin_update


class PinTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'lab').mkdir();(self.root/'docs'/'upstream').mkdir(parents=True)
        (self.root/'lab'/'images.lock.json').write_text(json.dumps(
            {'db':{'tag':'postgres:17-alpine','id':'sha256:'+'a'*64},
             'auth':{'tag':'v2.170.0','id':'sha256:'+'b'*64}}))
        (self.root/'lab'/'distro-image.lock.json').write_text(json.dumps(
            {'tag':'supabase:2026','id':'sha256:'+'c'*64}))
        (self.root/'lab'/'storage-image.lock.json').write_text(json.dumps(
            {'tag':'storage:v1.0.0','id':'sha256:'+'d'*64}))
        (self.root/'lab'/'studio-image.lock.json').write_text(json.dumps(
            {'studio':{'tag':'studio:2026.09.07','id':'sha256:'+'e'*64},'meta':{'tag':'postgres-meta:v0.99.0','id':'sha256:'+'f'*64}}))
        (self.root/'lab'/'realtime-image.lock.json').write_text(json.dumps(
            {'tag':'realtime:v2.138.1','id':'sha256:'+'1'*64}))
        self.patches=[patch.object(pin_update,'ROOT',self.root)]
        for item in self.patches:item.start();self.addCleanup(item.stop)

    def test_components_lists_every_pin_with_its_digest(self):
        found=pin_update.components()
        self.assertIn(('lab/images.lock.json','db'),found)
        self.assertIn(('lab/distro-image.lock.json','id'),found)
        self.assertEqual(found[('lab/storage-image.lock.json','id')]['id'],'sha256:'+'d'*64)

    def test_verify_accepts_pinned_digests(self):
        self.assertTrue(pin_update.verify())

    def test_verify_rejects_a_floating_tag(self):
        lock=json.loads((self.root/'lab'/'images.lock.json').read_text())
        lock['db']={'tag':'postgres:latest','id':'sha256:'+'a'*64}
        (self.root/'lab'/'images.lock.json').write_text(json.dumps(lock))
        self.assertFalse(pin_update.verify())

    def test_verify_rejects_an_unpinned_digest(self):
        lock=json.loads((self.root/'lab'/'images.lock.json').read_text())
        lock['auth']={'tag':'v2.170.0','id':'v2.170.0'}
        (self.root/'lab'/'images.lock.json').write_text(json.dumps(lock))
        self.assertFalse(pin_update.verify())

    def test_stage_writes_the_review_entry_before_changing_the_pin(self):
        pin_update.stage('lab/images.lock.json','db','postgres:17.1','sha256:'+'f'*64,'security release')
        lock=json.loads((self.root/'lab'/'images.lock.json').read_text())
        self.assertEqual(lock['db']['id'],'sha256:'+'f'*64)
        self.assertEqual(lock['db']['tag'],'postgres:17.1')
        entries=list((self.root/'docs'/'upstream').glob('*db*.md'))
        self.assertEqual(len(entries),1)
        text=entries[0].read_text()
        self.assertIn('sha256:'+'a'*64,text)
        self.assertIn('rollback',text.lower())

    def test_stage_refuses_floating_tags_and_unchanged_digests(self):
        with self.assertRaises(SystemExit):pin_update.stage('lab/images.lock.json','db','postgres:latest','sha256:'+'f'*64,'x')
        with self.assertRaises(SystemExit):pin_update.stage('lab/images.lock.json','db','postgres:17.1','sha256:'+'a'*64,'x')
        with self.assertRaises(SystemExit):pin_update.stage('lab/images.lock.json','db','postgres:17.1','postgres:17.1','x')
        with self.assertRaises(SystemExit):pin_update.stage('lab/images.lock.json','missing','postgres:17.1','sha256:'+'f'*64,'x')

    def test_stage_refuses_a_second_entry_for_the_same_version(self):
        pin_update.stage('lab/images.lock.json','db','postgres:17.1','sha256:'+'f'*64,'first')
        with self.assertRaises(SystemExit):
            pin_update.stage('lab/images.lock.json','db','postgres:17.1','sha256:'+'e'*64,'second')

    def test_only_one_component_changes_per_invocation(self):
        pin_update.stage('lab/images.lock.json','db','postgres:17.1','sha256:'+'f'*64,'x')
        lock=json.loads((self.root/'lab'/'images.lock.json').read_text())
        self.assertEqual(lock['auth']['id'],'sha256:'+'b'*64)


if __name__=='__main__':unittest.main()