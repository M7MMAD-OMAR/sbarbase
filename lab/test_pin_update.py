"""Pin update policy enforcement: one component, pinned digest, review entry first."""
import contextlib
import datetime
import io
import json
from pathlib import Path
from unittest.mock import patch
import tempfile
import unittest
import image_identity
import pin_update


class PinTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'lab').mkdir();(self.root/'docs'/'upstream').mkdir(parents=True)
        (self.root/'lab'/'images.lock.json').write_text(json.dumps(
            {'db':{'tag':'postgres:17-alpine','id':'sha256:'+'a'*64,'digests':['postgres@sha256:'+'a'*64]},
             'auth':{'tag':'gotrue:v2.170.0','id':'sha256:'+'b'*64,'digests':['gotrue@sha256:'+'b'*64]},
             'rest':{'tag':'postgrest:v14.15','id':'sha256:'+'3'*64,'digests':['postgrest@sha256:'+'3'*64]}}))
        (self.root/'lab'/'distro-image.lock.json').write_text(json.dumps(
            {'tag':'supabase:2026','id':'sha256:'+'c'*64,'digests':['supabase@sha256:'+'c'*64]}))
        (self.root/'lab'/'storage-image.lock.json').write_text(json.dumps(
            {'tag':'storage:v1.0.0','id':'sha256:'+'d'*64,'digests':['storage@sha256:'+'d'*64]}))
        (self.root/'lab'/'studio-image.lock.json').write_text(json.dumps(
            {'studio':{'tag':'studio:2026.09.07','id':'sha256:'+'e'*64,'digests':['studio@sha256:'+'e'*64]},'meta':{'tag':'postgres-meta:v0.99.0','id':'sha256:'+'f'*64,'digests':['postgres-meta@sha256:'+'f'*64]}}))
        (self.root/'lab'/'realtime-image.lock.json').write_text(json.dumps(
            {'tag':'realtime:v2.138.1','id':'sha256:'+'1'*64,'digests':['realtime@sha256:'+'1'*64]}))
        (self.root/'lab'/'functions-image.lock.json').write_text(json.dumps(
            {'tag':'edge-runtime:v1.77.0','id':'sha256:'+'2'*64,'digests':['edge-runtime@sha256:'+'2'*64]}))
        self.patches=[patch.object(pin_update,'ROOT',self.root)]
        for item in self.patches:item.start();self.addCleanup(item.stop)
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))  # keep the unittest summary last

    def test_components_lists_every_pin_with_its_digest(self):
        found=pin_update.components()
        self.assertIn(('lab/images.lock.json','db'),found)
        self.assertIn(('lab/distro-image.lock.json','id'),found)
        self.assertEqual(found[('lab/storage-image.lock.json','id')]['id'],'sha256:'+'d'*64)

    def test_verify_accepts_pinned_digests(self):
        self.assertTrue(pin_update.verify())

    def test_verify_rejects_a_floating_tag(self):
        lock=json.loads((self.root/'lab'/'images.lock.json').read_text())
        lock['db']={'tag':'postgres:latest','id':'sha256:'+'a'*64,'digests':['postgres@sha256:'+'a'*64]}
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


    def test_stage_accepts_a_flat_lock_and_rewrites_its_digest_proof(self):
        pin_update.stage('lab/realtime-image.lock.json',None,'realtime:v2.143.4','sha256:'+'9'*64,'patch')
        lock=json.loads((self.root/'lab'/'realtime-image.lock.json').read_text())
        self.assertEqual((lock['tag'],lock['id']),('realtime:v2.143.4','sha256:'+'9'*64))
        self.assertEqual(lock['digests'],['realtime@sha256:'+'9'*64])
        self.assertEqual(image_identity.reference(lock),'docker.io/library/realtime@sha256:'+'9'*64)
        entries=list((self.root/'docs'/'upstream').glob('*-realtime-realtime-v2.143.4.md'))
        self.assertEqual(len(entries),1)
        self.assertIn('sha256:'+'1'*64,entries[0].read_text())

    def test_stage_rewrites_stale_digest_proof_of_a_keyed_lock(self):
        lock=json.loads((self.root/'lab'/'images.lock.json').read_text())
        lock['auth']={'tag':'public.ecr.aws/supabase/gotrue:v2.196.0','id':'sha256:'+'b'*64,
                      'digests':['public.ecr.aws/supabase/gotrue@sha256:'+'b'*64]}
        (self.root/'lab'/'images.lock.json').write_text(json.dumps(lock))
        pin_update.stage('lab/images.lock.json','auth','public.ecr.aws/supabase/gotrue:v2.197.0','sha256:'+'7'*64,'x')
        staged=json.loads((self.root/'lab'/'images.lock.json').read_text())['auth']
        self.assertEqual(staged['digests'],['public.ecr.aws/supabase/gotrue@sha256:'+'7'*64])
        self.assertEqual(image_identity.reference(staged),'public.ecr.aws/supabase/gotrue@sha256:'+'7'*64)

    def test_stage_keeps_the_tag_when_only_the_digest_moves(self):
        pin_update.stage('lab/images.lock.json','db',None,'sha256:'+'8'*64,'same tag rebuilt')
        lock=json.loads((self.root/'lab'/'images.lock.json').read_text())
        self.assertEqual(lock['db']['tag'],'postgres:17-alpine')
        self.assertEqual(lock['db']['digests'],['postgres@sha256:'+'8'*64])

    def test_stage_refuses_a_different_repository(self):
        with self.assertRaises(SystemExit):
            pin_update.stage('lab/realtime-image.lock.json',None,'evil/realtime:v2.143.4','sha256:'+'9'*64,'x')
        with self.assertRaises(SystemExit):
            pin_update.stage('lab/images.lock.json','auth','other:v2.171.0','sha256:'+'9'*64,'x')
        self.assertEqual(json.loads((self.root/'lab'/'realtime-image.lock.json').read_text())['id'],'sha256:'+'1'*64)
        self.assertEqual(list((self.root/'docs'/'upstream').glob('*.md')),[])

    def test_stage_refuses_a_component_name_on_a_flat_lock_and_none_on_a_keyed_lock(self):
        with self.assertRaises(SystemExit):
            pin_update.stage('lab/realtime-image.lock.json','db','realtime:v2.143.4','sha256:'+'9'*64,'x')
        with self.assertRaises(SystemExit):
            pin_update.stage('lab/images.lock.json',None,'postgres:17.1','sha256:'+'9'*64,'x')

    def test_stage_refuses_a_version_tag_the_identity_rules_reject(self):
        with self.assertRaises(SystemExit):
            pin_update.stage('lab/realtime-image.lock.json',None,'realtime:v2/../x','sha256:'+'9'*64,'x')
        self.assertEqual(list((self.root/'docs'/'upstream').glob('*.md')),[])

    def test_verify_rejects_a_lock_whose_digest_proof_is_stale(self):
        lock=json.loads((self.root/'lab'/'realtime-image.lock.json').read_text())
        lock['id']='sha256:'+'9'*64
        (self.root/'lab'/'realtime-image.lock.json').write_text(json.dumps(lock))
        self.assertFalse(pin_update.verify())

    def test_floating_tags_are_refused_in_any_case(self):
        with self.assertRaises(SystemExit):pin_update.stage('lab/images.lock.json','db','postgres:Main','sha256:'+'9'*64,'x')
        lock=json.loads((self.root/'lab'/'images.lock.json').read_text())
        lock['db']['tag']='postgres:Stable'
        (self.root/'lab'/'images.lock.json').write_text(json.dumps(lock))
        self.assertFalse(pin_update.verify())

    def test_any_failure_while_writing_keeps_the_old_lock_and_leaves_no_entry_so_a_retry_works(self):
        before=(self.root/'lab'/'realtime-image.lock.json').read_text()
        for failure in (OSError('disk full'),KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__):
                with patch.object(pin_update.os,'replace',side_effect=failure):
                    with self.assertRaises(type(failure)):
                        pin_update.stage('lab/realtime-image.lock.json',None,'realtime:v2.143.4','sha256:'+'9'*64,'x')
                self.assertEqual((self.root/'lab'/'realtime-image.lock.json').read_text(),before)
                self.assertEqual(list((self.root/'docs'/'upstream').glob('*.md')),[])
                self.assertEqual(list((self.root/'lab').glob('*.staging')),[])
        pin_update.stage('lab/realtime-image.lock.json',None,'realtime:v2.143.4','sha256:'+'9'*64,'retry')

    def test_verify_reports_a_pin_with_a_missing_or_malformed_id(self):
        for broken in ({'tag':'gotrue:v2.170.0','id':None},{'tag':'gotrue:v2.170.0'},{'tag':'gotrue:v2.170.0','id':12}):
            lock=json.loads((self.root/'lab'/'images.lock.json').read_text())
            lock['auth']=broken
            (self.root/'lab'/'images.lock.json').write_text(json.dumps(lock))
            self.assertFalse(pin_update.verify(),broken)

    def test_verify_reports_a_malformed_lock_instead_of_crashing(self):
        (self.root/'lab'/'storage-image.lock.json').write_text('{not json')
        self.assertFalse(pin_update.verify())

    def test_an_equivalent_repository_spelling_is_the_same_repository(self):
        pin_update.stage('lab/images.lock.json','db','docker.io/library/postgres:17.6','sha256:'+'9'*64,'x')
        lock=json.loads((self.root/'lab'/'images.lock.json').read_text())['db']
        self.assertEqual((lock['tag'],lock['digests']),('postgres:17.6',['postgres@sha256:'+'9'*64]))

    def test_the_review_entry_is_plain_markdown(self):
        pin_update.stage('lab/realtime-image.lock.json',None,'realtime:v2.143.4','sha256:'+'9'*64,'patch')
        text=next((self.root/'docs'/'upstream').glob('*.md')).read_text()
        self.assertTrue(text.startswith('# Upstream update: realtime realtime:v2.143.4\n\n- Date: '))
        self.assertIn('- Previous pin (rollback): `realtime:v2.138.1` at `sha256:'+'1'*64+'`',text)
        self.assertIn('\n## Rollback\n',text)
        self.assertIn('\n## Decision\n',text)

    def test_stage_refuses_when_the_current_pin_is_floating_or_invalid(self):
        lock=json.loads((self.root/'lab'/'images.lock.json').read_text())
        lock['db']['tag']='postgres:Main'
        (self.root/'lab'/'images.lock.json').write_text(json.dumps(lock))
        with self.assertRaises(SystemExit):pin_update.stage('lab/images.lock.json','db',None,'sha256:'+'9'*64,'x')
        self.assertEqual(list((self.root/'docs'/'upstream').glob('*.md')),[])

    def test_stage_refuses_malformed_input_without_a_traceback(self):
        with self.assertRaises(SystemExit):pin_update.stage('lab/realtime-image.lock.json',None,'realtime','sha256:'+'9'*64,'x')
        (self.root/'lab'/'storage-image.lock.json').write_text('[]')
        with self.assertRaises(SystemExit):pin_update.stage('lab/storage-image.lock.json',None,'storage:v1.1.0','sha256:'+'9'*64,'x')
        (self.root/'lab'/'storage-image.lock.json').write_text('{oops')
        with self.assertRaises(SystemExit):pin_update.stage('lab/storage-image.lock.json',None,'storage:v1.1.0','sha256:'+'9'*64,'x')
        self.assertEqual(list((self.root/'docs'/'upstream').glob('*.md')),[])

    def test_verify_reports_a_removed_or_non_object_keyed_pin(self):
        for broken in (None,'sha256:'+'a'*64,[]):
            lock=json.loads((self.root/'lab'/'images.lock.json').read_text())
            lock['db']=broken
            (self.root/'lab'/'images.lock.json').write_text(json.dumps(lock))
            self.assertFalse(pin_update.verify(),broken)

    def test_show_lists_invalid_pins_without_crashing(self):
        lock=json.loads((self.root/'lab'/'images.lock.json').read_text())
        lock['auth']={'tag':None};lock['db']=None
        (self.root/'lab'/'images.lock.json').write_text(json.dumps(lock))
        pin_update.show()
        (self.root/'lab'/'storage-image.lock.json').write_text('{oops')
        with self.assertRaises(SystemExit):pin_update.show()

    def test_verify_reports_a_deleted_component_key(self):
        self.assertTrue(pin_update.verify())
        lock=json.loads((self.root/'lab'/'images.lock.json').read_text())
        del lock['rest']
        (self.root/'lab'/'images.lock.json').write_text(json.dumps(lock))
        self.assertFalse(pin_update.verify())

    def test_floating_means_the_version_part_only(self):
        self.assertTrue(pin_update.floating('postgres:latest-alpine'))
        for version in ('latest_alpine','latest.1','main_2026','stable.17','Edge-1','master'):
            self.assertTrue(pin_update.floating('postgres:'+version),version)
        for version in ('mainline1','stablecoin','17-alpine','v2.143.4'):
            self.assertFalse(pin_update.floating('postgres:'+version),version)
        with self.assertRaises(SystemExit):pin_update.stage('lab/images.lock.json','db','postgres:latest_alpine','sha256:'+'9'*64,'x')
        self.assertTrue(pin_update.floating('registry:5000/x/postgres:Edge'))
        self.assertFalse(pin_update.floating('main:1.0'))
        self.assertFalse(pin_update.floating('registry:5000/x/postgres'))
        self.assertFalse(pin_update.floating('postgres:17-alpine'))

    def test_verify_holds_each_lock_to_the_shape_the_runtime_reads(self):
        pin=json.loads((self.root/'lab'/'realtime-image.lock.json').read_text())
        (self.root/'lab'/'realtime-image.lock.json').write_text(json.dumps({'realtime':pin}))
        self.assertFalse(pin_update.verify())
        (self.root/'lab'/'realtime-image.lock.json').write_text(json.dumps(pin))
        self.assertTrue(pin_update.verify())
        keyed=json.loads((self.root/'lab'/'images.lock.json').read_text())
        (self.root/'lab'/'images.lock.json').write_text(json.dumps(dict(keyed['db'],**keyed)))
        self.assertFalse(pin_update.verify())

    def test_stage_refuses_a_lock_in_the_wrong_shape_for_its_file(self):
        pin=json.loads((self.root/'lab'/'realtime-image.lock.json').read_text())
        (self.root/'lab'/'realtime-image.lock.json').write_text(json.dumps({'realtime':pin}))
        with self.assertRaises(SystemExit):pin_update.stage('lab/realtime-image.lock.json','realtime','realtime:v2.143.4','sha256:'+'9'*64,'x')
        with self.assertRaises(SystemExit):pin_update.stage('lab/realtime-image.lock.json',None,'realtime:v2.143.4','sha256:'+'9'*64,'x')
        self.assertEqual(list((self.root/'docs'/'upstream').glob('*.md')),[])

    def test_stage_refuses_a_keyed_lock_that_would_not_verify(self):
        lock=json.loads((self.root/'lab'/'images.lock.json').read_text())
        del lock['auth']
        (self.root/'lab'/'images.lock.json').write_text(json.dumps(lock))
        with self.assertRaises(SystemExit):pin_update.stage('lab/images.lock.json','rest','postgrest:v14.16','sha256:'+'9'*64,'x')
        lock=json.loads((self.root/'lab'/'images.lock.json').read_text())
        lock['auth']={'tag':'gotrue:v2.170.0','id':'sha256:'+'b'*64,'digests':['gotrue@sha256:'+'b'*64]};lock['digests']=[]
        (self.root/'lab'/'images.lock.json').write_text(json.dumps(lock))
        with self.assertRaises(SystemExit):pin_update.stage('lab/images.lock.json','rest','postgrest:v14.16','sha256:'+'9'*64,'x')
        self.assertEqual(list((self.root/'docs'/'upstream').glob('*.md')),[])

    def test_a_second_entry_for_a_version_is_refused_on_a_later_day(self):
        pin_update.stage('lab/images.lock.json','rest','postgrest:v14.16','sha256:'+'9'*64,'first')
        later=datetime.date.today()+datetime.timedelta(days=1)
        with patch.object(pin_update.datetime,'date',wraps=datetime.date) as fake:
            fake.today.return_value=later
            with self.assertRaises(SystemExit):
                pin_update.stage('lab/images.lock.json','rest','postgrest:v14.16','sha256:'+'8'*64,'second')
        self.assertEqual(len(list((self.root/'docs'/'upstream').glob('*-rest-postgrest-v14.16.md'))),1)

    def test_the_recorded_rollback_restores_a_pin_that_verify_accepts(self):
        before=json.loads((self.root/'lab'/'realtime-image.lock.json').read_text())
        pin_update.stage('lab/realtime-image.lock.json',None,'realtime:v2.140.0','sha256:'+'9'*64,'first')
        pin_update.stage('lab/realtime-image.lock.json',None,'realtime:v2.141.0','sha256:'+'8'*64,'second')
        text=next((self.root/'docs'/'upstream').glob('*-realtime-realtime-v2.141.0.md')).read_text()
        recorded=json.loads(text.split('```json\n',1)[1].split('\n```',1)[0])
        self.assertEqual(recorded['tag'],'realtime:v2.140.0')
        (self.root/'lab'/'realtime-image.lock.json').write_text(json.dumps(recorded))
        self.assertTrue(pin_update.verify())
        first=next((self.root/'docs'/'upstream').glob('*-realtime-realtime-v2.140.0.md')).read_text()
        self.assertEqual(json.loads(first.split('```json\n',1)[1].split('\n```',1)[0]),before)

if __name__=='__main__':unittest.main()