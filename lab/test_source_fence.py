import unittest
from types import SimpleNamespace
from source_fence import fence,is_fenced


class FenceTests(unittest.TestCase):
    def test_refusal_precedes_session_termination_and_retry_is_safe(self):
        calls=[];blocked=False
        def sql(query):
            nonlocal blocked
            calls.append(query)
            if query.startswith('ALTER DATABASE'):blocked=True
            value='t' if query.startswith('SELECT NOT') and blocked else '1' if 'count(*) FROM pg_database' in query else '0'
            return SimpleNamespace(stdout=value)
        e='e_'+'a'*24
        self.assertTrue(fence(sql,e)['connections_refused'])
        self.assertLess(next(i for i,q in enumerate(calls) if q.startswith('ALTER')),next(i for i,q in enumerate(calls) if 'pg_terminate_backend' in q))
        self.assertTrue(fence(sql,e)['connections_refused'])

    def test_termination_failure_leaves_persistent_refusal(self):
        calls=[]
        def sql(query):
            calls.append(query)
            if 'pg_terminate_backend' in query:raise RuntimeError('termination unavailable')
            return SimpleNamespace(stdout='1')
        with self.assertRaises(RuntimeError):fence(sql,'e_'+'a'*24)
        self.assertTrue(any('ALLOW_CONNECTIONS false' in q for q in calls))
        self.assertFalse(any('ALLOW_CONNECTIONS true' in q for q in calls))

    def test_invalid_identifier_never_reaches_sql(self):
        def sql(query):self.fail('Unexpected SQL')
        with self.assertRaises(ValueError):fence(sql,'postgres;DROP DATABASE x')

    def test_export_fence_journals_original_logins_before_disabling(self):
        import json
        from source_fence import prepare_export
        e='e_'+'b'*24;events=[]
        def sql(query):
            events.append(query)
            if 'jsonb_agg' in query:return SimpleNamespace(stdout=json.dumps([{'name':e+'_'+k,'login':True} for k in ('auth','rest','storage')]))
            return SimpleNamespace(stdout='f' if query.startswith('SELECT NOT') else '0')
        def persist(value):events.append('persist:'+value['phase'])
        result=prepare_export(sql,e,persist)
        self.assertEqual(result['phase'],'services-fenced')
        self.assertLess(events.index('persist:preparing'),next(i for i,v in enumerate(events) if v.startswith('BEGIN;')))

    def test_export_fence_failure_does_not_reenable_logins(self):
        import json
        from source_fence import prepare_export
        e='e_'+'b'*24;queries=[];saved=[]
        def sql(query):
            queries.append(query)
            if 'jsonb_agg' in query:return SimpleNamespace(stdout=json.dumps([{'name':e+'_'+k,'login':True} for k in ('auth','rest','storage')]))
            if 'pg_terminate_backend' in query:raise RuntimeError('failure')
            return SimpleNamespace(stdout='f' if query.startswith('SELECT NOT') else '0')
        with self.assertRaises(RuntimeError):prepare_export(sql,e,lambda value:saved.append(dict(value)))
        self.assertEqual(saved[-1]['phase'],'preparing')
        self.assertFalse(any(' LOGIN;' in q for q in queries))
