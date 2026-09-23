"""Pressure parsing, refusal policy, the repeated series, the response and ordering."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch
import pressure_admission as pressure
import resource_admission
import durable_runtime as runtime


class PressureTests(unittest.TestCase):
    def setUp(self):
        self.good = {name: {metric: 0.0 for metric in pressure.THRESHOLDS} for name in pressure.CONTAINERS}

    def test_parse_expected_line(self):
        sample='some avg10=5.25 avg60=2.50 avg300=0.10 total=999\nfull avg10=1.25 avg60=0.0 avg300=0.0 total=4\n'
        self.assertEqual(pressure.avg10(sample, 'some'), 5.25)
        self.assertEqual(pressure.avg10(sample, 'full'), 1.25)

    def test_bad_pressure_cannot_look_idle(self):
        for sample in ('', 'some avg60=0', 'some avg10=nan', 'some avg10=inf', 'some avg10=-1', 'some avg10=101', 'some avg10=0\nsome avg10=0'):
            with self.subTest(sample=sample), self.assertRaises((ValueError, KeyError)):
                pressure.avg10(sample, 'some')

    def test_each_pressure_boundary_refuses(self):
        self.assertIsNone(pressure.refusal(self.good))
        for container in pressure.CONTAINERS:
            for metric, threshold in pressure.THRESHOLDS.items():
                changed={name:dict(values) for name,values in self.good.items()}
                changed[container][metric]=threshold
                self.assertEqual(pressure.refusal(changed), metric)

    def test_incomplete_snapshot_fails_closed(self):
        with self.assertRaises(ValueError):
            pressure.refusal({})
        self.good[pressure.CONTAINERS[0]]['cpu_some10']=float('nan')
        with self.assertRaises(ValueError):
            pressure.refusal(self.good)

    def test_pressure_refusal_precedes_credentials(self):
        target=runtime.Runtime.__new__(runtime.Runtime)
        target.values={'environments':{}}
        self.good[pressure.CONTAINERS[0]]['cpu_some10']=75.0
        with patch.object(runtime, 'inspect', return_value={'owned':True}), patch.object(runtime, 'owned_usage_bytes', return_value=0), patch.object(runtime.resource_policy, 'restart_fits', return_value=True), patch.object(resource_admission, 'snapshot'), patch.object(resource_admission, 'refusal', return_value=None), patch.object(pressure, 'snapshot', return_value=self.good), patch.object(runtime, 'atomic') as persist:
            with self.assertRaises(runtime.AdmissionLimitError):
                target.provision('e_'+'c'*24)
            self.assertEqual(target.values['environments'], {})
            persist.assert_not_called()


class SeriesTests(unittest.TestCase):
    """Sampling and summarising, against fixed readings and no host at all."""

    def reading(self, cpu=0.0, io=0.0, memory=0.0, name=None):
        return {name or pressure.CONTAINERS[0]: {'cpu_some10': cpu, 'io_full10': io, 'memory_full10': memory}}

    def record(self, readings, interval=5.0, name=None):
        return {'containers': [name or pressure.CONTAINERS[0]], 'interval_seconds': interval,
                'window_seconds': interval*len(readings), 'readings': readings}

    def seconds(self, values, name=None):
        return [{'index': index, 'at_seconds': index*5.0, 'values': value}
                for index, value in enumerate(values)]

    def test_series_takes_a_bounded_number_of_readings(self):
        taken = []
        tick = iter(range(0, 1000))
        series = pressure.series(10.0, 1.0, containers=(pressure.CONTAINERS[0],),
                                 read=lambda: self.reading(),
                                 sleep=lambda seconds: taken.append(seconds),
                                 clock=lambda: float(next(tick)))
        self.assertEqual([item['index'] for item in series['readings']], list(range(10)))
        self.assertEqual(series['interval_seconds'], 1.0)
        self.assertEqual(series['window_seconds'], 10.0)
        self.assertEqual(series['containers'], list(pressure.CONTAINERS[:1]))
        # Nine sleeps for ten readings, and no sleep after the last one.
        self.assertEqual(taken, [1.0]*9)
        # Each reading records the instant it started and how long it took.
        self.assertEqual(series['readings'][0]['at_seconds'], 1.0)
        self.assertEqual(series['readings'][0]['read_seconds'], 1.0)
        self.assertEqual(series['readings'][-1]['at_seconds'], 19.0)
        self.assertEqual(series['elapsed_seconds'], 20.0)

    def test_series_refuses_bounds_outside_the_configured_limits(self):
        bounds = ((0.0, 1.0), (10.0, 0.0), (10.0, pressure.MIN_INTERVAL_SECONDS/2),
                  (1.0, 1.0), (pressure.MAX_WINDOW_SECONDS+1, 5.0), (float('nan'), 1.0))
        for window, interval in bounds:
            with self.subTest(window=window, interval=interval), self.assertRaises(ValueError):
                pressure.series(window, interval, containers=(pressure.CONTAINERS[0],),
                                read=lambda: self.reading(), sleep=lambda seconds: None)

    def test_a_series_cannot_keep_an_incomplete_reading(self):
        for incomplete in ({}, self.reading(cpu=float('nan')), self.reading(cpu=101.0), {'other': dict() }):
            with self.subTest(reading=incomplete), self.assertRaises(ValueError):
                pressure.series(4.0, 2.0, containers=(pressure.CONTAINERS[0],),
                                read=lambda incomplete=incomplete: incomplete, sleep=lambda seconds: None)

    def test_series_summarises_a_fixed_set_of_readings(self):
        name = pressure.CONTAINERS[0]
        values = [self.reading(cpu=cpu) for cpu in (10.0, 30.0, 55.0, 80.0, 40.0, 5.0)]
        summary = pressure.summarise(self.record(self.seconds(values)))
        self.assertEqual(summary['count'], 6)
        self.assertEqual(summary['window_seconds'], 30.0)
        self.assertEqual(summary['observed_seconds'], 25.0)
        self.assertAlmostEqual(summary['avg10'][name]['cpu_some10'], 220.0/6)
        self.assertEqual(summary['max10'][name]['cpu_some10'], 80.0)
        self.assertEqual(summary['last10'][name]['cpu_some10'], 5.0)
        self.assertEqual(summary['avg10'][name]['io_full10'], 0.0)
        self.assertEqual(summary['crossings'],
                         [{'container': name, 'metric': 'cpu_some10', 'threshold': 50.0, 'samples_over': 2,
                           'first_index': 2, 'last_index': 3, 'span_seconds': 5.0, 'duration_seconds': 10.0,
                           'first_at_seconds': 10.0, 'last_at_seconds': 15.0, 'peak': 80.0}])
        self.assertTrue(summary['crossed'])
        self.assertEqual(summary['peak'], {'container': name, 'metric': 'cpu_some10', 'value': 80.0})
        # The last reading is below every threshold, so nothing would be refused now.
        self.assertIsNone(summary['latest_refusal'])

    def test_a_crossing_that_is_still_present_refuses_now(self):
        name = pressure.CONTAINERS[0]
        values = [self.reading(cpu=cpu) for cpu in (10.0, 30.0, 55.0, 80.0, 40.0, 60.0)]
        summary = pressure.summarise(self.record(self.seconds(values)))
        self.assertEqual(summary['latest_refusal'], 'cpu_some10')
        self.assertEqual(summary['crossings'][0]['samples_over'], 3)
        self.assertEqual(summary['crossings'][0]['span_seconds'], 15.0)
        self.assertEqual(summary['crossings'][0]['duration_seconds'], 20.0)

    def test_a_threshold_is_reached_exactly_and_not_only_passed(self):
        values = [self.reading(io=io) for io in (0.0, 20.0, 20.0, 0.0)]
        summary = pressure.summarise(self.record(self.seconds(values)))
        self.assertEqual(summary['crossings'][0]['metric'], 'io_full10')
        self.assertEqual(summary['crossings'][0]['samples_over'], 2)
        self.assertEqual(summary['latest_refusal'], None)

    def test_a_quiet_series_reports_no_crossing(self):
        summary = pressure.summarise(self.record(self.seconds([self.reading() for _ in range(3)])))
        self.assertFalse(summary['crossed'])
        self.assertEqual(summary['crossings'], [])
        self.assertIsNone(summary['latest_refusal'])
        self.assertEqual(summary['peak']['value'], 0.0)

    def test_a_summary_needs_readings_and_an_interval(self):
        with self.assertRaises(ValueError):
            pressure.summarise(self.record([]))
        record = self.record(self.seconds([self.reading(), self.reading()]))
        record['interval_seconds'] = 0
        with self.assertRaises(ValueError):
            pressure.summarise(record)


class ResponseTests(unittest.TestCase):
    """The response the design supports today: refuse and record, nothing else."""

    def summary(self, last_cpu):
        name = pressure.CONTAINERS[0]
        values = [{'index': index, 'at_seconds': index*5.0, 'values': {name: {'cpu_some10': cpu, 'io_full10': 0.0, 'memory_full10': 0.0}}}
                  for index, cpu in enumerate((10.0, 30.0, 55.0, 80.0, 40.0, last_cpu))]
        return pressure.summarise({'containers': [name], 'interval_seconds': 5.0, 'window_seconds': 30.0,
                                   'readings': values})

    def test_response_refuses_new_admissions_while_over_and_records_durably(self):
        name = pressure.CONTAINERS[0]
        with tempfile.TemporaryDirectory() as folder:
            ledger = Path(folder)/'pressure-crossings.jsonl'
            decision = pressure.response(self.summary(60.0), ledger, now=lambda: 1234.5)
            self.assertEqual(decision['action'], 'refuse_new_admissions')
            self.assertTrue(decision['refuse'])
            self.assertEqual(decision['reason'], 'pressure_cpu_some10')
            self.assertEqual(decision['container'], name)
            self.assertEqual(decision['over_threshold_seconds'], 20.0)
            self.assertTrue(decision['record']['ok'])
            self.assertEqual(decision['record']['path'], str(ledger))
            # The scope that is deliberately out of reach travels with the decision.
            self.assertTrue(any('pause' in entry for entry in decision['not_implemented']))
            lines = ledger.read_text().splitlines()
            self.assertEqual(len(lines), 1)
            record = json.loads(lines[0])
            self.assertEqual(record['container'], name)
            self.assertEqual(record['metric'], 'cpu_some10')
            self.assertEqual(record['threshold'], 50.0)
            self.assertEqual(record['peak'], 80.0)
            self.assertEqual(record['samples_over'], 3)
            self.assertEqual(record['duration_seconds'], 20.0)
            self.assertEqual(record['span_seconds'], 15.0)
            self.assertEqual(record['at'], 1234.5)
            # A crossing that has ended is recorded and does not refuse.
            ended = pressure.response(self.summary(5.0), ledger, now=lambda: 1240.0)
            self.assertEqual(ended['action'], 'admit')
            self.assertIsNone(ended['reason'])
            self.assertIsNone(ended['over_threshold_seconds'])
            self.assertTrue(ended['crossed'])
            self.assertEqual(len(ledger.read_text().splitlines()), 2)

    def test_response_without_a_crossing_records_nothing(self):
        name = pressure.CONTAINERS[0]
        quiet = pressure.summarise({'containers': [name], 'interval_seconds': 5.0, 'window_seconds': 10.0,
                                    'readings': [{'index': index, 'at_seconds': index*5.0,
                                                  'values': {name: {'cpu_some10': 0.0, 'io_full10': 0.0, 'memory_full10': 0.0}}}
                                                 for index in range(2)]})
        with tempfile.TemporaryDirectory() as folder:
            ledger = Path(folder)/'pressure-crossings.jsonl'
            decision = pressure.response(quiet, ledger)
            self.assertEqual(decision['action'], 'admit')
            self.assertEqual(decision['record']['records'], [])
            self.assertTrue(decision['record']['ok'])
            self.assertFalse(ledger.exists())

    def test_a_recording_failure_is_reported_and_does_not_change_the_decision(self):
        name = pressure.CONTAINERS[0]
        with tempfile.TemporaryDirectory() as folder:
            decision = pressure.response(self.summary(60.0), Path(folder))
            self.assertEqual(decision['action'], 'refuse_new_admissions')
            self.assertFalse(decision['record']['ok'])
            self.assertEqual(decision['record']['error'], 'IsADirectoryError')
            self.assertEqual(len(decision['record']['records']), 1)


class SnapshotContractTests(unittest.TestCase):
    """The admission-time snapshot keeps its containers, paths, thresholds and shape."""

    def docker_for(self, cgroupns='private', cpu=12.5, io=0.25, memory=2.0):
        def fake(*args):
            if args[0] == 'inspect':
                return json.dumps({'CgroupnsMode': cgroupns})
            return {'/sys/fs/cgroup/cpu.pressure': 'some avg10=%s avg60=0.0 avg300=0.0 total=1\nfull avg10=0.0 avg60=0.0 avg300=0.0 total=0\n' % cpu,
                    '/sys/fs/cgroup/io.pressure': 'some avg10=0.0 avg60=0.0 avg300=0.0 total=0\nfull avg10=%s avg60=0.0 avg300=0.0 total=1\n' % io,
                    '/sys/fs/cgroup/memory.pressure': 'some avg10=%s avg60=0.0 avg300=0.0 total=1\nfull avg10=%s avg60=0.0 avg300=0.0 total=1\n' % (memory, memory)}[args[-1]]
        return fake

    def test_thresholds_and_container_tuple_are_unchanged(self):
        self.assertEqual(pressure.THRESHOLDS, {'cpu_some10': 50.0, 'io_full10': 20.0, 'memory_full10': 1.0})
        self.assertEqual(pressure.CONTAINERS, ('sbarbase-durable-db', 'sbarbase-durable-storage'))

    def test_snapshot_reads_the_same_shape_from_the_same_paths(self):
        with patch.object(pressure, 'docker', side_effect=self.docker_for()) as fake:
            measured = pressure.snapshot()
        expected = {name: {'cpu_some10': 12.5, 'io_full10': 0.25, 'memory_full10': 2.0} for name in pressure.CONTAINERS}
        self.assertEqual(measured, expected)
        expected_calls = []
        for name in pressure.CONTAINERS:
            expected_calls += [('inspect', '--format', '{{json .HostConfig}}', name),
                               ('exec', name, 'cat', '/sys/fs/cgroup/cpu.pressure'),
                               ('exec', name, 'cat', '/sys/fs/cgroup/io.pressure'),
                               ('exec', name, 'cat', '/sys/fs/cgroup/memory.pressure')]
        self.assertEqual([item.args for item in fake.call_args_list], expected_calls)

    def test_snapshot_still_requires_a_private_cgroup_namespace(self):
        with patch.object(pressure, 'docker', side_effect=self.docker_for(cgroupns='host')):
            with self.assertRaises(RuntimeError):
                pressure.snapshot()

    def test_snapshot_accepts_an_explicit_container_tuple(self):
        with patch.object(pressure, 'docker', side_effect=self.docker_for()) as fake:
            measured = pressure.snapshot(('sbarbase-pressure-probe-test',))
        self.assertEqual(list(measured), ['sbarbase-pressure-probe-test'])
        self.assertEqual(fake.call_args_list[0], call('inspect', '--format', '{{json .HostConfig}}',
                                                      'sbarbase-pressure-probe-test'))

    def test_refusal_accepts_an_explicit_container_tuple(self):
        name = 'sbarbase-pressure-probe-test'
        values = {name: {metric: 0.0 for metric in pressure.THRESHOLDS}}
        self.assertIsNone(pressure.refusal(values, (name,)))
        values[name]['memory_full10'] = 1.0
        self.assertEqual(pressure.refusal(values, (name,)), 'memory_full10')


if __name__ == '__main__':
    unittest.main()