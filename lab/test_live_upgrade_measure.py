import json
import unittest

import live_upgrade_measure as measure


class ClassifyTests(unittest.TestCase):
    same = (0, '2026-10-26T10:00:00Z')

    def test_a_refusal_names_the_condition(self):
        logs = 'x\nThe handover is not used (Q4: a pinned image changes); the console restarts as it always has.\n'
        found = measure.classify_handover(logs, self.same, (1, '2026-10-26T10:01:00Z'), 'confirmed')
        self.assertEqual(found['result'], 'refused')
        self.assertEqual(found['reason'], 'Q4: a pinned image changes')
        self.assertTrue(found['container_restarted'])

    def test_no_handover_line_and_a_container_restart_is_the_planned_exit(self):
        logs = 'Sbarbase moved to another version and stopped so it can start on it.'
        found = measure.classify_handover(logs, self.same, (1, 'later'), 'confirmed')
        self.assertEqual(found['result'], 'restarted')

    def test_the_handover_ran_when_the_container_never_restarted(self):
        found = measure.classify_handover('Finishing provisioning', self.same, self.same, 'confirmed')
        self.assertEqual(found, {'result': 'handover', 'reason': None, 'container_restarted': False})

    def test_unknown_without_a_confirmation(self):
        self.assertEqual(measure.classify_handover('', self.same, self.same, 'applied')['result'], 'unknown')

    def test_a_handover_that_failed_after_the_round(self):
        logs = 'The handover failed at H7: an answer came from the old process\n'
        found = measure.classify_handover(logs, self.same, (1, 'later'), 'confirmed')
        self.assertEqual((found['result'], found['reason']), ('failed', 'H7: an answer came from the old process'))

    def test_an_exception_in_the_decision_is_a_refusal(self):
        found = measure.classify_handover('The handover is not used: OSError: boom\n', self.same, (1, 'later'), 'confirmed')
        self.assertEqual((found['result'], found['reason']), ('refused', 'OSError: boom'))


class HelperTests(unittest.TestCase):
    def test_scrub_removes_tokens_and_keys(self):
        text = 'a eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc-def_123 b sb_publishable_abc123 c password=hunter2 d "secret": "xyz"'
        cleaned = measure.scrub(text)
        for word in ('eyJhbGci', 'sb_publishable_abc123', 'hunter2', 'xyz'):
            self.assertNotIn(word, cleaned)
        self.assertIn('[token]', cleaned)

    def test_doc_release_adds_one_comment_line(self):
        text = measure.doc_release('# Title\n\nBody\n', 'v0.9.1')
        self.assertEqual(text, '# Title\n\nBody\n\n<!-- live upgrade measure: v0.9.1 -->\n')
        self.assertNotEqual(text, measure.doc_release('# Title\n\nBody\n', 'v0.9.2'))

    def test_last_json_ignores_other_lines(self):
        self.assertEqual(measure.last_json('noise\n{"a": 1}\nmore noise\n'), {'a': 1})
        self.assertIsNone(measure.last_json('nothing\n{broken'))
        self.assertEqual(json.dumps(measure.last_json('{"applied": true}')), '{"applied": true}')

    def test_every_stage_has_a_method(self):
        for stage in measure.STAGES:
            self.assertTrue(hasattr(measure.Run, 'stage_' + stage.replace('-', '_')), stage)

    def test_unknown_stages_are_refused(self):
        with self.assertRaises(SystemExit):
            measure.main(['run', '--out', '/nonexistent/o', '--private', '/nonexistent/p', '--stages', 'nonsense'])


if __name__ == '__main__':
    unittest.main()
