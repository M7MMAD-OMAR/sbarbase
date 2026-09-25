"""Every GitHub Actions step runs a commit, not a tag that can be moved."""
from pathlib import Path
import re
import unittest

WORKFLOWS=Path(__file__).resolve().parent.parent/'.github'/'workflows'
USES=re.compile(r'^\s*(?:-\s+)?uses:\s*(\S+)(.*)$')
PINNED=re.compile(r'^[\w.-]+/[\w./-]+@[0-9a-f]{40}$')


class ActionPinTests(unittest.TestCase):
    def steps(self):
        found=[]
        for path in sorted(WORKFLOWS.glob('*.yml')):
            for number,line in enumerate(path.read_text().splitlines(),1):
                match=USES.match(line)
                if match:found.append((f'{path.name}:{number}',match.group(1),match.group(2).strip()))
        return found

    def test_the_workflows_use_actions(self):
        self.assertTrue(self.steps())

    def test_every_action_is_pinned_to_a_full_commit_sha_with_its_tag_named(self):
        for where,reference,rest in self.steps():
            if reference.startswith('./'):continue
            self.assertRegex(reference,PINNED,where)
            self.assertRegex(rest,r'^#\s*v\d',where+': name the tag the commit was resolved from')


if __name__=='__main__':unittest.main()
