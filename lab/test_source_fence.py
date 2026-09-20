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
