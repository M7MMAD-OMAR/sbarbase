"""Acceptance must reject missing artifacts and unsupported completion claims."""
import importlib.util
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('plan_acceptance', ROOT / 'deploy/check_plan.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class PlanAcceptance(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        # Copy the validation inputs, not unrelated diagrams or runtime evidence.
        names = [*checker.DOCUMENTS, 'docs/engineering/gauntlet-ledger.json',
                 'docs/engineering/benchmarks/supabase-v0.8.2.source.json']
        for name in names:
            destination = self.root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, destination)
        # Link validation needs target existence, not the target's contents.
        for name in checker.DOCUMENTS:
            for target in re.findall(r'\]\(([^)]+)\)', (self.root / name).read_text()):
                if '://' in target or target.startswith('#'):
                    continue
                destination = (self.root / name).parent / target.split('#', 1)[0]
                destination = destination.resolve()
                if destination.is_relative_to(self.root) and not destination.exists():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.touch()

    def test_current_planning_artifacts_are_consistent(self):
        self.assertEqual(checker.check(self.root), [])

    def test_a_missing_goal_prevents_acceptance(self):
        (self.root / 'PROJECT_GOAL.md').unlink()
        self.assertTrue(any('cannot read' in error for error in checker.check(self.root)))

    def test_local_external_sources_are_not_accepted(self):
        with (self.root / 'PROJECT_GOAL.md').open('a') as file:
            file.write('\n[private dependency](../outside-project.md)\n')
        self.assertTrue(any('outside repository' in error for error in checker.check(self.root)))

    def test_missing_links_and_prohibited_characters_are_reported(self):
        with (self.root / 'PROJECT_GOAL.md').open('a') as file:
            file.write('\n[missing](missing.md)\n' + chr(0x2014) + ' \n')
        errors = checker.check(self.root)
        self.assertTrue(any('missing local link' in error for error in errors))
        self.assertTrue(any('prohibited dash' in error for error in errors))
        self.assertTrue(any('trailing whitespace' in error for error in errors))

    def test_partial_slice_inventory_cannot_claim_completion(self):
        path = self.root / 'docs/engineering/gauntlet-ledger.json'
        value = json.loads(path.read_text())
        value['status'] = 'complete'
        path.write_text(json.dumps(value))
        self.assertTrue(any('unproven slices' in error for error in checker.check(self.root)))

    def test_passed_slice_requires_an_evidence_reference(self):
        path = self.root / 'docs/engineering/gauntlet-ledger.json'
        value = json.loads(path.read_text())
        value['slices'][0]['status'] = 'passed'
        path.write_text(json.dumps(value))
        self.assertTrue(any('no evidence reference' in error for error in checker.check(self.root)))

    def test_malformed_ledger_is_a_visible_refusal(self):
        path = self.root / 'docs/engineering/gauntlet-ledger.json'
        path.write_text('[]')
        self.assertTrue(any('invalid or missing ledger' in error for error in checker.check(self.root)))


if __name__ == '__main__':
    unittest.main()
