"""Upstream drift report: scheme-aware comparison, compose bar, digest drift and honest failure. No network."""
import contextlib
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pin_update
import upstream_watch as watch

ECR='public.ecr.aws/supabase'
COMPOSE_TEXT='''services:
  studio:
    image: supabase/studio:2026.09.07-sha-7996410
  rest:
    image: postgrest/postgrest:v14.17
  db:
    image: supabase/postgres:17.6.1.136
'''


def digest(char):
    return 'sha256:'+char*64


class FakeUpstream:
    """Routes registry v2 and compose requests to in-memory data; records nothing it was not given."""

    def __init__(self,tags,digests,compose=COMPOSE_TEXT,broken=(),pages=None):
        self.tags,self.digests,self.compose,self.broken,self.pages=tags,digests,compose,set(broken),pages or {}
        self.calls=[]

    def __call__(self,url,headers=None,method='GET'):
        self.calls.append((method,url))
        if url==watch.COMPOSE:
            if 'compose' in self.broken:raise watch.LookupFailed('compose unreachable')
            return {},self.compose.encode()
        if '/token' in url:return {},json.dumps({'token':'t'}).encode()
        for repository in self.broken:
            if f'/v2/{repository}/' in url:raise watch.LookupFailed('registry down for '+repository)
        if '/manifests/' in url:
            repository,tag=url.split('/v2/',1)[1].split('/manifests/')
            return {'Docker-Content-Digest':self.digests[(repository,tag)]},b''
        repository=url.split('/v2/',1)[1].split('/tags/list')[0]
        if url in self.pages:return self.pages[url]
        return {},json.dumps({'name':repository,'tags':self.tags[repository]}).encode()


class WatchTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);(self.root/'lab').mkdir()
        self.upstream=self.root/'docs'/'upstream';self.upstream.mkdir(parents=True)
        self.write('images.lock.json',{'db':{'tag':'postgres:17-alpine','id':digest('a')},
                                       'rest':{'tag':f'{ECR}/postgrest:v14.15','id':digest('b')},
                                       'auth':{'tag':f'{ECR}/gotrue:v2.197.0','id':digest('9')}})
        self.write('distro-image.lock.json',{'tag':f'{ECR}/postgres:17.6.1.166','id':digest('c')})
        self.write('studio-image.lock.json',{'studio':{'tag':f'{ECR}/studio:2026.09.07-sha-7996410','id':digest('d')},
                                             'meta':{'tag':f'{ECR}/postgres-meta:v0.100.0','id':digest('f')}})
        locks={name:pin_update.LOCKS[name] for name in ('lab/images.lock.json','lab/distro-image.lock.json','lab/studio-image.lock.json')}
        for item in (patch.object(pin_update,'ROOT',self.root),patch.object(pin_update,'LOCKS',locks)):
            item.start();self.addCleanup(item.stop)
        self.tags={'library/postgres':['17-alpine','17','18-alpine'],
                   'supabase/postgrest':['v14.15','v14.16','v14.18','v16.0','v16.4','v14.18-rc.1','devel'],
                   'supabase/postgres':['17.6.1.166','17.11.0.004','17.11.0.005-rc.2','17.11.0.004-orioledb','18.1.0.001'],
                   'supabase/studio':['2026.09.07-sha-7996410','2026.10.12-sha-3699278','v1.24.05','latest'],
                   'supabase/postgres-meta':['v0.99.0','v0.100.0'],'supabase/gotrue':['v2.196.0','v2.197.0']}
        self.digests={('library/postgres','17-alpine'):digest('a'),('supabase/postgrest','v14.15'):digest('b'),
                      ('supabase/postgres','17.6.1.166'):digest('c'),('supabase/studio','2026.09.07-sha-7996410'):digest('d'),
                      ('supabase/postgres-meta','v0.100.0'):digest('f'),('supabase/gotrue','v2.197.0'):digest('9')}

    def write(self,name,value):
        def proven(pin):  # real locks carry the repository digest proof image_identity requires
            return dict(pin,digests=[pin['tag'].rsplit(':',1)[0]+'@'+pin['id']])
        value=proven(value) if 'tag' in value else {key:proven(pin) for key,pin in value.items()}
        (self.root/'lab'/name).write_text(json.dumps(value))

    def rows(self,result):
        return {row['component']:row for row in result['components']}

    def test_tags_parse_into_registry_repository_and_version(self):
        self.assertEqual(watch.parse_tag(f'{ECR}/gotrue:v2.196.0'),('public.ecr.aws','supabase/gotrue','v2.196.0'))
        self.assertEqual(watch.parse_tag('postgres:17-alpine'),('docker.io','library/postgres','17-alpine'))
        self.assertEqual(watch.parse_tag('postgrest/postgrest:v14.17'),('docker.io','postgrest/postgrest','v14.17'))

    def test_candidates_keep_only_stable_tags_of_the_pinned_scheme(self):
        self.assertEqual(watch.candidates('17.6.1.166',self.tags['supabase/postgres']),['17.6.1.166','17.11.0.004','18.1.0.001'])
        self.assertEqual(watch.candidates('v14.15',self.tags['supabase/postgrest']),['v14.15','v14.16','v14.18','v16.0','v16.4'])
        # Tag shapes change upstream: a three-part v13 pin must still see the two-part v14 and v16 lines.
        self.assertEqual(watch.newer('v13.0.7',watch.candidates('v13.0.7',['v13.0.7','v13.0.8','v14.17','2.9'])),['v13.0.8','v14.17'])
        self.assertEqual(watch.newer('v2.9.11',watch.candidates('v2.9.11',['v2.9.11','2.9.12','2.10.0'])),['2.9.12','2.10.0'])
        # The retired v1.x Studio naming must never outrank dated builds.
        self.assertEqual(watch.candidates('2026.09.07-sha-7996410',self.tags['supabase/studio']),
                         ['2026.09.07-sha-7996410','2026.10.12-sha-3699278'])
        self.assertEqual(watch.candidates('17-alpine',self.tags['library/postgres']),['17-alpine','18-alpine'])
        self.assertIsNone(watch.candidates('bookworm',['bookworm','trixie']))

    def test_change_size_is_classified(self):
        self.assertEqual(watch.change('v14.15','v16.4'),'major')
        self.assertEqual(watch.change('17.6.1.166','17.11.0.004'),'minor')
        self.assertEqual(watch.change('v1.77.0','v1.77.4'),'patch')
        self.assertEqual(watch.change('2026.09.07-sha-7996410','2026.10.12-sha-3699278'),'newer')

    def test_compose_bar_matches_by_repository_without_confusing_library_images(self):
        compose=watch.compose_pins(COMPOSE_TEXT)
        self.assertEqual(watch.compose_version('supabase/postgres',compose),'17.6.1.136')
        self.assertEqual(watch.compose_version('supabase/postgrest',compose),'v14.17')
        self.assertIsNone(watch.compose_version('library/postgres',compose))

    def test_a_major_jump_reports_the_line_and_the_newest_separately(self):
        rows=self.rows(watch.report(FakeUpstream(self.tags,self.digests),self.upstream))
        rest=rows['rest']
        self.assertEqual((rest['newest_in_line'],rest['newest'],rest['change'],rest['compose']),('v14.18','v16.4','major','v14.17'))
        self.assertEqual(rest['unreviewed'],['v14.18','v16.4'])
        self.assertEqual(rest['status'],'behind')
        self.assertEqual((rows['distro']['newest_in_line'],rows['distro']['newest']),('17.11.0.004','18.1.0.001'))

    def test_drift_without_a_review_entry_exits_three(self):
        result=watch.report(FakeUpstream(self.tags,self.digests),self.upstream)
        self.assertEqual(result['exit'],watch.EXIT_DRIFT)
        self.assertEqual(self.rows(result)['studio']['newest'],'2026.10.12-sha-3699278')

    def test_review_entries_named_by_stage_settle_the_drift(self):
        for name in ('rest-postgrest-v14.18','rest-postgrest-v16.4','distro-postgres-17.11.0.004',
                     'distro-postgres-18.1.0.001','studio-studio-2026.10.12-sha-3699278','db-postgres-18-alpine'):
            (self.upstream/f'2026-10-14-{name}.md').write_text('# review\n')
        self.digests[('library/postgres','17-alpine')]=digest('a')
        result=watch.report(FakeUpstream(self.tags,self.digests),self.upstream)
        self.assertEqual({r['component']:r['status'] for r in result['components']},
                         {'db':'reviewed','rest':'reviewed','distro':'reviewed','studio':'reviewed','meta':'current','auth':'current'})
        self.assertEqual(result['exit'],watch.EXIT_CURRENT)

    def test_a_moved_digest_under_the_pinned_tag_is_drift(self):
        self.digests[('library/postgres','17-alpine')]=digest('e')
        rows=self.rows(watch.report(FakeUpstream(self.tags,self.digests),self.upstream))
        self.assertTrue(rows['db']['digest_moved'])
        self.assertEqual((rows['db']['status'],rows['db']['scheme']),('moved','major'))

    def test_a_moving_major_tag_reports_a_later_major_line(self):
        db=self.rows(watch.report(FakeUpstream(self.tags,self.digests),self.upstream))['db']
        self.assertEqual((db['newest_in_line'],db['newest'],db['change'],db['status']),(None,'18-alpine','major','behind'))

    def test_a_failed_lookup_is_never_reported_as_current_and_exits_two(self):
        result=watch.report(FakeUpstream(self.tags,self.digests,broken={'supabase/studio'}),self.upstream)
        rows=self.rows(result)
        self.assertEqual(rows['studio']['status'],'failed')
        self.assertIn('registry down',rows['studio']['error'])
        self.assertEqual(rows['rest']['status'],'behind')  # the rest of the report still arrives
        self.assertEqual(result['exit'],watch.EXIT_FAILED)

    def test_a_missing_compose_file_is_a_failure_not_an_empty_bar(self):
        result=watch.report(FakeUpstream(self.tags,self.digests,broken={'compose'}),self.upstream)
        self.assertEqual(result['compose_error'],'compose unreachable')
        self.assertEqual(result['exit'],watch.EXIT_FAILED)
        self.assertTrue(all(row['compose'] is None for row in result['components']))

    def test_tag_lists_follow_pagination(self):
        first='https://public.ecr.aws/v2/supabase/postgrest/tags/list?n=1000'
        pages={first:({'Link':'</v2/supabase/postgrest/tags/list?last=v14.16&n=1000>; rel="next"'},
                      json.dumps({'tags':['v14.15','v14.16']}).encode()),
               'https://public.ecr.aws/v2/supabase/postgrest/tags/list?last=v14.16&n=1000':({},json.dumps({'tags':['v14.19']}).encode())}
        registry=watch.Registry(FakeUpstream(self.tags,self.digests,pages=pages))
        self.assertEqual(registry.tags('public.ecr.aws','supabase/postgrest'),['v14.15','v14.16','v14.19'])

    def test_transport_errors_become_lookup_failures(self):
        with patch.object(watch.urllib.request,'urlopen',side_effect=urllib.error.URLError('no route')):
            with self.assertRaises(watch.LookupFailed):watch.fetch('https://public.ecr.aws/token/')
        with patch.object(watch.urllib.request,'urlopen',side_effect=TimeoutError('slow')):
            with self.assertRaises(watch.LookupFailed):watch.fetch('https://public.ecr.aws/token/')

    def test_a_manifest_without_a_digest_header_fails(self):
        fake=FakeUpstream(self.tags,self.digests)
        self.digests[('supabase/postgrest','v14.15')]='v14.15'
        rows=self.rows(watch.report(fake,self.upstream))
        self.assertEqual(rows['rest']['status'],'failed')

    def test_main_prints_json_and_returns_the_report_exit(self):
        out=io.StringIO()
        with patch.object(watch,'report',return_value={'compose_source':watch.COMPOSE,'compose_error':None,'components':[],'exit':3}):
            with contextlib.redirect_stdout(out):code=watch.main(['--json'])
        self.assertEqual(code,3)
        self.assertEqual(json.loads(out.getvalue())['exit'],3)

    def test_an_invalid_pin_is_a_failure_row_not_a_crash(self):
        (self.root/'lab'/'studio-image.lock.json').write_text(json.dumps({'studio':{'tag':f'{ECR}/studio:2026.09.07-sha-7996410'}}))
        result=watch.report(FakeUpstream(self.tags,self.digests),self.upstream)
        self.assertEqual(self.rows(result)['studio']['status'],'failed')
        self.assertIn('invalid pin',self.rows(result)['studio']['error'])
        self.assertEqual(result['exit'],watch.EXIT_FAILED)

    def test_an_unreadable_lock_file_fails_its_own_row_only(self):
        (self.root/'lab'/'images.lock.json').write_text('{broken')
        result=watch.report(FakeUpstream(self.tags,self.digests),self.upstream)
        rows=self.rows(result)
        self.assertEqual(rows['lab/images.lock.json']['status'],'failed')
        self.assertIn('unreadable lock',rows['lab/images.lock.json']['error'])
        self.assertEqual(rows['distro']['status'],'behind')  # the readable locks are still reported
        self.assertEqual(result['exit'],watch.EXIT_FAILED)

    def test_a_misshapen_lock_is_a_failed_row_not_a_silent_gap(self):
        for broken in ({},[]):
            (self.root/'lab'/'distro-image.lock.json').write_text(json.dumps(broken))
            result=watch.report(FakeUpstream(self.tags,self.digests),self.upstream)
            self.assertEqual(self.rows(result)['lab/distro-image.lock.json']['status'],'failed',broken)
            self.assertEqual(result['exit'],watch.EXIT_FAILED)
        lock=json.loads((self.root/'lab'/'images.lock.json').read_text());del lock['rest']
        (self.root/'lab'/'images.lock.json').write_text(json.dumps(lock))
        rows=self.rows(watch.report(FakeUpstream(self.tags,self.digests),self.upstream))
        self.assertIn('rest is missing',rows['lab/images.lock.json']['error'])

    def test_a_dropped_connection_mid_body_is_a_lookup_failure(self):
        with patch.object(watch.urllib.request,'urlopen',side_effect=watch.http.client.IncompleteRead(b'partial')):
            with self.assertRaises(watch.LookupFailed):watch.fetch('https://public.ecr.aws/token/')

    def test_malformed_registry_answers_fail_the_row_not_the_run(self):
        self.tags['supabase/studio']=['2026.10.12-sha-3699278',7]
        self.tags['supabase/postgrest']='v14.18'
        rows=self.rows(watch.report(FakeUpstream(self.tags,self.digests),self.upstream))
        self.assertEqual((rows['studio']['status'],rows['rest']['status']),('failed','failed'))
        self.assertEqual(rows['distro']['status'],'behind')

    def test_a_null_token_fails_cleanly(self):
        def get(url,headers=None,method='GET'):
            if '/token' in url:return {},b'{"token":null}'
            return FakeUpstream(self.tags,self.digests)(url,headers,method)
        result=watch.report(get,self.upstream)
        self.assertTrue(all(r['status']=='failed' for r in result['components']))
        self.assertEqual(result['exit'],watch.EXIT_FAILED)

    def test_same_day_dated_builds_are_all_review_candidates(self):
        self.tags['supabase/studio']=['2026.09.07-sha-7996410','2026.09.07-sha-0000001','2026.10.12-sha-fffffff','2026.10.12-sha-0aaaaaa']
        rows=self.rows(watch.report(FakeUpstream(self.tags,self.digests),self.upstream))
        studio=rows['studio']
        self.assertEqual(studio['same_day_builds'],['2026.10.12-sha-0aaaaaa','2026.10.12-sha-fffffff'])
        self.assertEqual(studio['unreviewed'],['2026.10.12-sha-0aaaaaa','2026.10.12-sha-fffffff'])
        self.assertIn('2026.09.07-sha-0000001',watch.newer('2026.09.07-sha-7996410',watch.candidates('2026.09.07-sha-7996410',self.tags['supabase/studio'])))

    def test_a_tag_list_without_the_pinned_version_is_a_failure(self):
        for answer in (['v14.18','v16.4'],None):
            self.tags['supabase/postgrest']=answer
            rows=self.rows(watch.report(FakeUpstream(self.tags,self.digests),self.upstream))
            self.assertEqual(rows['rest']['status'],'failed',answer)
            self.assertRegex(rows['rest']['error'],'not listed|not a list')

    def test_a_pin_in_an_unknown_scheme_is_never_current(self):
        self.write('images.lock.json',{'db':{'tag':'postgres:17.6-alpine','id':digest('a')},
                                       'rest':{'tag':f'{ECR}/postgrest:v14.15','id':digest('b')},
                                       'auth':{'tag':f'{ECR}/gotrue:v2.197.0','id':digest('9')}})
        self.tags['library/postgres'].append('17.6-alpine')
        self.digests[('library/postgres','17.6-alpine')]=digest('a')
        result=watch.report(FakeUpstream(self.tags,self.digests),self.upstream)
        self.assertEqual(self.rows(result)['db']['status'],'failed')
        self.assertEqual(result['exit'],watch.EXIT_FAILED)

    def test_a_compose_file_without_images_is_a_failure(self):
        result=watch.report(FakeUpstream(self.tags,self.digests,compose='<html>not found</html>'),self.upstream)
        self.assertEqual(result['compose_error'],'the compose file lists no images')
        self.assertEqual(result['exit'],watch.EXIT_FAILED)

    def test_a_later_tag_page_without_tags_fails_instead_of_truncating(self):
        first='https://public.ecr.aws/v2/supabase/postgrest/tags/list?n=1000'
        pages={first:({'Link':'</v2/supabase/postgrest/tags/list?last=v14.15&n=1000>; rel="next"'},
                      json.dumps({'tags':['v14.15']}).encode()),
               'https://public.ecr.aws/v2/supabase/postgrest/tags/list?last=v14.15&n=1000':({},json.dumps({'errors':[{'code':'x'}]}).encode())}
        rows=self.rows(watch.report(FakeUpstream(self.tags,self.digests,pages=pages),self.upstream))
        self.assertEqual(rows['rest']['status'],'failed')

if __name__=='__main__':unittest.main()
