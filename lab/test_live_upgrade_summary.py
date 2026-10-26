import json
import tempfile
import unittest
from pathlib import Path

import live_upgrade_summary as summary


def row(t, kind='env', verdict='ok', ms=10.0, status=200, error=None):
    return {'t': t, 'ms': ms, 'kind': kind, 'status': status if verdict != 'error' else None,
            'error': error, 'verdict': verdict}


class PercentileTests(unittest.TestCase):
    def test_nearest_rank(self):
        values = list(range(1, 101))
        self.assertEqual(summary.percentile(values, .50), 50)
        self.assertEqual(summary.percentile(values, .95), 95)
        self.assertEqual(summary.percentile(values, .99), 99)
        self.assertEqual(summary.percentile([7], .99), 7)
        self.assertEqual(summary.percentile([3, 1, 2], .50), 2)
        self.assertIsNone(summary.percentile([], .50))


class GapTests(unittest.TestCase):
    def test_longest_gap_runs_from_one_arrival_to_the_next_start(self):
        rows = [row(0), row(0.2), row(0.4), row(5.4), row(5.6)]
        self.assertAlmostEqual(summary.longest_gap(rows, 0, 6), 4.99, places=2)

    def test_a_gap_that_spans_the_window_edge_counts(self):
        rows = [row(0), row(10)]
        self.assertAlmostEqual(summary.longest_gap(rows, 4, 5), 9.99, places=2)
        self.assertIsNone(summary.longest_gap([row(0)], 0, 5))

    def test_a_gap_wholly_outside_the_window_does_not(self):
        rows = [row(0), row(5), row(5.2), row(5.4)]
        self.assertAlmostEqual(summary.longest_gap(rows, 5.1, 5.3), 0.19, places=2)

    def test_failed_answers_do_not_close_a_gap(self):
        rows = [row(0), row(1, verdict='error', error='TimeoutError', ms=5000), row(2, verdict='held', status=503), row(3)]
        self.assertAlmostEqual(summary.longest_gap(rows, 0, 3), 2.99, places=2)

    def test_failure_run(self):
        rows = [row(0), row(1, verdict='error', ms=100), row(1.2, verdict='error', ms=100), row(1.4, verdict='held', status=503, ms=5), row(1.6)]
        self.assertAlmostEqual(summary.longest_failure_run(rows, 0, 3), 0.405, places=3)
        self.assertEqual(summary.longest_failure_run([row(0), row(1)], 0, 3), 0.0)


class SummarizeTests(unittest.TestCase):
    def readings(self):
        rows = []
        for tick in range(100):
            t = tick * 0.2
            failing = 5.0 <= t < 6.0
            rows.append(row(t, 'env', 'held' if failing else 'ok', ms=5 if failing else 10 + tick % 10, status=503 if failing else 200))
            rows.append(row(t, 'health', 'ok', ms=2))
        return rows

    def test_counts_latency_and_the_window(self):
        document = summary.summarize(self.readings(), [{'name': 'x', 'start': 4.0, 'end': 8.0, 'note': 'kept'}], {'runner': {'cpus': '4'}})
        scenario = document['scenarios'][0]
        env = scenario['stats']['env']
        self.assertEqual(scenario['note'], 'kept')
        self.assertEqual(document['runner'], {'cpus': '4'})
        self.assertEqual(env['n'], 21)
        self.assertEqual(env['held'], 5)
        self.assertEqual(env['failed'], 5)
        self.assertEqual(env['unexpected'], {'held:503': 5})
        self.assertEqual(env['latency_ms']['max'], 19)
        self.assertAlmostEqual(env['longest_gap_s'], 1.2, places=1)
        self.assertEqual(scenario['stats']['health']['failed'], 0)
        self.assertEqual(scenario['raw_columns'][0], 'seconds_from_start')
        self.assertTrue(all(len(item) == 6 for item in scenario['raw']))
        self.assertEqual(scenario['raw'][0][0], -4.0)

    def test_a_scenario_without_a_window_is_carried_untouched(self):
        document = summary.summarize(self.readings(), [{'name': 'y', 'status': 'blocked', 'reason': 'no docker', 'start': None, 'end': None}])
        self.assertEqual(document['scenarios'][0]['reason'], 'no docker')
        self.assertNotIn('stats', document['scenarios'][0])

    def test_wrong_answers_are_counted_apart_from_holds(self):
        rows = [row(0), row(0.2, verdict='wrong', status=401), row(0.4, verdict='error', error='ConnectionResetError', ms=1), row(0.6)]
        env = summary.kind_stats(rows, 'env', 0, 1)
        self.assertEqual((env['ok'], env['wrong'], env['error'], env['held'], env['failed']), (2, 1, 1, 0, 2))
        self.assertEqual(env['unexpected'], {'wrong:401': 1})
        self.assertEqual(env['errors'], {'ConnectionResetError': 1})

    def test_empty_input(self):
        document = summary.summarize([], [])
        self.assertEqual(document['scenarios'], [])
        self.assertEqual(document['all'], {})


class FileTests(unittest.TestCase):
    def test_load_skips_a_damaged_last_line_and_separates_marks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'pulse.jsonl'
            path.write_text('\n'.join([json.dumps({'kind': 'start', 't': 1}), json.dumps(row(2)), json.dumps({'kind': 'mark', 't': 3, 'name': 'a'}),
                                       json.dumps(row(1.5, 'health')), '{"kind": "env", "t": 4, "ms"']))
            readings, marks = summary.load_pulse(path)
        self.assertEqual([item['t'] for item in readings], [1.5, 2])
        self.assertEqual([item['name'] for item in marks], ['a'])

    def test_the_command_writes_the_document(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'pulse.jsonl').write_text('\n'.join(json.dumps(row(index * 0.2, kind)) for index in range(10) for kind in ('env', 'health')))
            (root / 'scenarios.json').write_text(json.dumps([{'name': 'z', 'start': 0.0, 'end': 1.0}]))
            (root / 'facts.json').write_text(json.dumps({'versions': {'base': 'v0.9.0'}}))
            summary.main(['--pulse', str(root / 'pulse.jsonl'), '--scenarios', str(root / 'scenarios.json'),
                          '--facts', str(root / 'facts.json'), '--out', str(root / 'out.json')])
            document = json.loads((root / 'out.json').read_text())
        self.assertEqual(document['versions'], {'base': 'v0.9.0'})
        self.assertEqual(document['scenarios'][0]['stats']['env']['n'], 6)
        self.assertEqual(document['readings']['total'], 20)


if __name__ == '__main__':
    unittest.main()
