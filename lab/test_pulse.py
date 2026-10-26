import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pulse


class Handler(BaseHTTPRequestHandler):
    mode = 'ok'
    seen = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        Handler.seen.append((self.path, self.headers.get('apikey'), self.headers.get('connection')))
        if self.path == '/health':
            body, status = {'status': 'ok'}, 200
        elif Handler.mode == 'held':
            body, status = {'message': 'held'}, 503
        elif Handler.mode == 'wrong':
            body, status = {'message': 'nope'}, 401
        else:
            body, status = [{'id': 1}], 200
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class JudgeTests(unittest.TestCase):
    def test_env_verdicts(self):
        self.assertEqual(pulse.judge('env', 200, '[{"id":1}]', None), 'ok')
        self.assertEqual(pulse.judge('env', 200, '[]', None), 'wrong')
        self.assertEqual(pulse.judge('env', 200, '{"id":1}', None), 'wrong')
        self.assertEqual(pulse.judge('env', 200, 'not json', None), 'wrong')
        self.assertEqual(pulse.judge('env', 503, '', None), 'held')
        self.assertEqual(pulse.judge('env', 401, '{}', None), 'wrong')
        self.assertEqual(pulse.judge('env', 502, '', None), 'wrong')
        self.assertEqual(pulse.judge('env', None, '', 'ConnectionRefusedError'), 'error')

    def test_health_verdicts(self):
        self.assertEqual(pulse.judge('health', 200, '{"status":"ok","held":false}', None), 'ok')
        self.assertEqual(pulse.judge('health', 200, '{"status":"starting"}', None), 'wrong')
        self.assertEqual(pulse.judge('health', 503, '{}', None), 'held')
        self.assertEqual(pulse.judge('health', None, '', 'TimeoutError'), 'error')

    def test_probe_records_the_fields(self):
        ticks = iter([10.0, 10.25])
        row = pulse.probe('env', 'http://x', '/p', {}, 1, clock=lambda: 1000.5, monotonic=lambda: next(ticks),
                          get=lambda *args: (200, '[1]', None))
        self.assertEqual(row, {'t': 1000.5, 'ms': 250.0, 'kind': 'env', 'status': 200, 'error': None, 'verdict': 'ok'})
        row = pulse.probe('env', 'http://x', '/p', {}, 1, get=lambda *args: (None, '', 'ConnectionResetError'))
        self.assertEqual((row['status'], row['error'], row['verdict']), (None, 'ConnectionResetError', 'error'))


class TargetTests(unittest.TestCase):
    def test_the_file_must_be_private_and_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'environment.json'
            path.write_text(json.dumps({'apiPath': '/e_1/', 'key': 'k', 'table': 't'}))
            path.chmod(0o644)
            with self.assertRaises(SystemExit):
                pulse.load_target(path)
            path.chmod(0o600)
            self.assertEqual(pulse.load_target(path), ('/e_1', 'k', 't'))
            path.write_text(json.dumps({'apiPath': 'e_1', 'key': 'k', 'table': 't'}))
            with self.assertRaises(SystemExit):
                pulse.load_target(path)


class RunTests(unittest.TestCase):
    def serve(self, mode):
        Handler.mode, Handler.seen = mode, []
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_address[1]}'

    def lines(self, mode, ticks=5):
        base = self.serve(mode)
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'pulse.jsonl'
            written = pulse.run(base, ('/e_1', 'secret-key', 'items'), out, interval=0.05, timeout=2, ticks=ticks)
            rows = [json.loads(line) for line in out.read_text().splitlines()]
            self.assertEqual(oct(out.stat().st_mode & 0o777), '0o600')
        return written, rows

    def test_two_probes_per_tick_and_a_start_and_an_end(self):
        written, rows = self.lines('ok')
        self.assertEqual(written, 10)
        self.assertEqual([row['kind'] for row in rows if row['kind'] in ('start', 'end')], ['start', 'end'])
        readings = [row for row in rows if row['kind'] in ('env', 'health')]
        self.assertEqual(sorted(row['kind'] for row in readings), ['env'] * 5 + ['health'] * 5)
        self.assertTrue(all(row['verdict'] == 'ok' and row['status'] == 200 for row in readings))
        self.assertTrue(all(row['ms'] >= 0 and row['error'] is None for row in readings))
        env = [seen for seen in Handler.seen if seen[0].startswith('/e_1/rest/v1/items')]
        self.assertEqual(len(env), 5)
        self.assertTrue(all(seen[1] == 'secret-key' and seen[2] == 'close' for seen in env))
        self.assertFalse(any(seen[1] for seen in Handler.seen if seen[0] == '/health'))

    def test_the_key_is_never_written_to_the_output(self):
        _, rows = self.lines('ok', 2)
        self.assertNotIn('secret-key', json.dumps(rows))

    def test_a_hold_and_a_wrong_answer_are_told_apart(self):
        _, rows = self.lines('held', 2)
        self.assertEqual({row['verdict'] for row in rows if row['kind'] == 'env'}, {'held'})
        _, rows = self.lines('wrong', 2)
        self.assertEqual({row['verdict'] for row in rows if row['kind'] == 'env'}, {'wrong'})

    def test_nothing_listening_is_an_error_not_a_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'pulse.jsonl'
            pulse.run('http://127.0.0.1:1', ('/e_1', 'k', 't'), out, interval=0.05, timeout=1, ticks=2)
            rows = [json.loads(line) for line in out.read_text().splitlines() if '"env"' in line or '"health"' in line]
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(row['verdict'] == 'error' and row['status'] is None and row['error'] for row in rows))

    def test_stop_ends_the_run_and_writes_the_end_line(self):
        base = self.serve('ok')
        stop = threading.Event()
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'pulse.jsonl'
            thread = threading.Thread(target=pulse.run, args=(base, ('/e_1', 'k', 't'), out),
                                      kwargs={'interval': 0.05, 'timeout': 2, 'stop': stop})
            thread.start()
            threading.Timer(0.3, stop.set).start()
            thread.join(10)
            self.assertFalse(thread.is_alive())
            self.assertEqual(json.loads(out.read_text().splitlines()[-1])['kind'], 'end')

    def test_mark_appends_a_line_to_the_same_file(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'pulse.jsonl'
            pulse.mark(out, 'handover:begin')
            pulse.mark(out, 'handover:end')
            rows = [json.loads(line) for line in out.read_text().splitlines()]
        self.assertEqual([row['name'] for row in rows], ['handover:begin', 'handover:end'])
        self.assertTrue(all(row['kind'] == 'mark' and row['t'] > 0 for row in rows))


if __name__ == '__main__':
    unittest.main()
