"""Every relative link in the documentation must resolve, and no long dash may appear.

The documentation is split into explain, guides, reference, decisions and an
engineering notebook. Moving a page breaks every relative link that pointed at
it, and a broken link is found by a reader, not by a build. This check parses
every Markdown file under docs/ (except the machine-written evidence), the root
README.md and CLAUDE.md, resolves each relative link target against the file
that contains it and fails listing every target that does not exist.

The project also forbids the em dash and the en dash in its prose, so the same
Markdown set is checked for U+2014 and U+2013.
"""
import re
import unittest
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / 'docs'
EVIDENCE = DOCS / 'evidence'
LONG_DASHES = ('\u2014', '\u2013')

FENCE = re.compile(r'^\s*(```|~~~)')
INLINE_CODE = re.compile(r'`+[^`\n]*`+')
# [text](target) and ![alt](target); the target may be wrapped in <...> and may
# carry a "title" suffix.
INLINE_LINK = re.compile(r'!?\[(?:[^\[\]]|\[[^\]]*\])*\]\(\s*(<[^>]*>|[^)\s]+)(?:\s+"[^"]*"|\s+\'[^\']*\')?\s*\)')
REFERENCE_DEFINITION = re.compile(r'^\s{0,3}\[[^\]]+\]:\s*(<[^>]*>|\S+)')
EXTERNAL = re.compile(r'^(?:[a-z][a-z0-9+.-]*:|//)', re.IGNORECASE)


def markdown_files():
    files = [path for path in sorted(DOCS.rglob('*.md')) if EVIDENCE not in path.parents]
    return files + [ROOT / 'README.md', ROOT / 'CLAUDE.md']


def prose_lines(text):
    """Yield (line number, line) outside fenced blocks, with inline code removed."""
    fenced = False
    for number, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            yield number, INLINE_CODE.sub('', line)


def link_targets(text):
    for number, line in prose_lines(text):
        for match in INLINE_LINK.finditer(line):
            yield number, match.group(1)
        definition = REFERENCE_DEFINITION.match(line)
        if definition:
            yield number, definition.group(1)


def missing_target(source, target):
    """Return the unresolved path for a relative target, or None when it resolves or is not relative."""
    target = target.strip()
    if target.startswith('<') and target.endswith('>'):
        target = target[1:-1].strip()
    if not target or target.startswith('#') or EXTERNAL.match(target):
        return None
    path = unquote(target.split('#', 1)[0].split('?', 1)[0])
    if not path:
        return None
    resolved = (ROOT / path.lstrip('/')) if path.startswith('/') else (source.parent / path)
    return None if resolved.exists() else resolved


class DocumentationLinkTests(unittest.TestCase):
    def test_every_relative_link_resolves(self):
        broken = []
        for source in markdown_files():
            for number, target in link_targets(source.read_text(encoding='utf-8')):
                if missing_target(source, target) is not None:
                    broken.append(f'{source.relative_to(ROOT)}:{number}: {target}')
        self.assertEqual(broken, [], 'relative links whose target does not exist')

    def test_no_long_dash_in_the_documentation(self):
        found = []
        for source in markdown_files():
            for number, line in enumerate(source.read_text(encoding='utf-8').splitlines(), 1):
                if any(dash in line for dash in LONG_DASHES):
                    found.append(f'{source.relative_to(ROOT)}:{number}')
        self.assertEqual(found, [], 'em or en dash found; use a comma, colon, semicolon or split the sentence')


class CheckerSelfTests(unittest.TestCase):
    """The checker must not wave through the cases it exists to catch."""

    def test_external_anchor_and_code_links_are_ignored(self):
        text = '[a](https://x.y) [b](mailto:a@b.c) [c](#top) `[d](missing.md)`\n```\n[e](missing.md)\n```\n'
        self.assertEqual([t for _, t in link_targets(text) if missing_target(DOCS / 'x.md', t)], [])

    def test_a_missing_relative_target_is_reported(self):
        source = DOCS / 'README.md'
        self.assertIsNotNone(missing_target(source, 'no-such-page.md#part'))
        self.assertIsNone(missing_target(source, 'evidence/#anything'))

    def test_titles_angle_brackets_and_reference_definitions_are_parsed(self):
        text = '[a](<one two.md> "t") ![b](pic.png \'t\')\n[ref]: three.md\n'
        self.assertEqual([t for _, t in link_targets(text)], ['<one two.md>', 'pic.png', 'three.md'])


if __name__ == '__main__':
    unittest.main()
