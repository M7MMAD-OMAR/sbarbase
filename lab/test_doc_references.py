"""The deployment documents must name files that exist, and evidence that agrees.

A runbook that points at a script that was renamed, or at evidence a run never
wrote, is worse than no runbook: the operator finds out on the server. These
checks read the operational documents, require every path they name to exist,
and require the acceptance handoff copy to agree with the rehearsal it copies.
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / 'docs'
# The live operational documents: an operator or the next agent follows these.
# Review records and the point-in-time checkpoints are deliberately out of scope,
# because they must keep saying what was true when they were written.
DOCUMENTS = (DOCS / 'guides' / 'server-deployment.md', DOCS / 'guides' / 'operator-setup.md', DOCS / 'guides' / 'cli.md',
             DOCS / 'guides' / 'backup-and-restore.md', DOCS / 'guides' / 'upgrades.md',
             DOCS / 'guides' / 'local-lab.md', DOCS / 'reference' / 'deployment-readiness.md',
             DOCS / 'reference' / 'status.md', DOCS / 'reference' / 'configuration.md',
             DOCS / 'engineering' / 'INDEPENDENT-RESTORE.md', DOCS / 'engineering' / 'UPSTREAM-UPDATE-POLICY.md',
             DOCS / 'engineering' / 'handoff' / 'README.md')
# The Arabic versions of the guides and reference pages name the same files.
DOCUMENTS += tuple(document.with_name(document.name[:-len('.md')] + '.ar.md') for document in DOCUMENTS
                   if document.parent.name in ('guides', 'reference'))
# Not preceded by a word character or a dot, so a runtime path such as
# .lab/rendered-sbarbase.service is not read as the repository's lab/ directory.
PATH_PATTERN = re.compile(r'(?<![\w.])((?:lab|deploy)/[A-Za-z0-9_./-]+\.(?:py|ts|sh|service))\b')
EVIDENCE_PATTERN = re.compile(r'docs/evidence/([a-z0-9-]+)\.json')


def documents():
    # Keyed by the path inside the repository, so two files that share a name
    # (every README.md) cannot silently replace each other.
    return {str(document.relative_to(ROOT)): document.read_text() for document in DOCUMENTS}


class DocumentReferenceTests(unittest.TestCase):
    def test_every_script_the_documents_name_exists(self):
        missing = sorted({match.group(1) for text in documents().values()
                          for match in PATH_PATTERN.finditer(text)
                          if not (ROOT / match.group(1)).exists()})
        self.assertEqual(missing, [], 'the runbook names files that are not in the checkout')

    def test_every_evidence_file_the_documents_name_exists(self):
        missing = sorted({name for text in documents().values() for name in EVIDENCE_PATTERN.findall(text)
                          if not (ROOT / 'docs' / 'evidence' / f'{name}.json').exists()})
        self.assertEqual(missing, [], 'the documents cite evidence that was never written')

    def test_the_acceptance_handoff_copy_agrees_with_the_rehearsal_it_copies(self):
        rehearsal = json.loads((ROOT / 'docs' / 'evidence' / 'server-acceptance-rehearsal.json').read_text())
        latest = json.loads((ROOT / 'docs' / 'evidence' / 'server-acceptance-latest.json').read_text())
        self.assertTrue(rehearsal['passed'])
        self.assertTrue(latest['passed'])
        self.assertEqual(latest['count'], rehearsal['count'])
        self.assertEqual(len(latest['checks']), latest['count'], 'the recorded count must match the checks it holds')
        self.assertEqual([row['check'] for row in latest['checks']],
                         [row['check'] for row in rehearsal['checks']],
                         'the handoff copy must be the rehearsal it claims to be')

    def test_the_evidence_this_project_commits_has_no_failed_check(self):
        for name in ('server-acceptance-latest', 'server-acceptance-rehearsal', 'supervisor-unit',
                     'console-serve', 'tls-termination'):
            record = json.loads((ROOT / 'docs' / 'evidence' / f'{name}.json').read_text())
            failed = [row['check'] for row in record.get('checks', []) if not row.get('ok', True)]
            self.assertEqual(failed, [], f'{name} records a failed check')


if __name__ == '__main__':
    unittest.main()
