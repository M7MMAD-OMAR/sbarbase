"""Reader-facing documentation exists in English and Arabic, and the pairs agree.

Each page in the bilingual set has an Arabic sibling with the .ar.md suffix
(docs/engineering/ARABIC-DOCS.md). A page added in English only, or an Arabic page
left behind when its English page is removed, fails here. Commands must not drift
in translation, so the fenced code blocks of each pair are compared exactly, and
each file opens with a link to the other.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / 'docs'
# Single files at fixed paths, plus directories whose every page is bilingual.
FILES = ('README.md', 'SECURITY.md', 'CONTRIBUTING.md', 'CHANGELOG.md', 'lab/README.md',
         'docs/README.md', 'docs/decisions/README.md', 'docs/design/CONSOLE.md', 'docs/design/CONSOLE-QA.md')
DIRECTORIES = ('docs/guides', 'docs/explain', 'docs/reference')
FENCE = re.compile(r'^\s*(```|~~~)')


def arabic_path(english):
    return english.with_name(english.name[:-len('.md')] + '.ar.md')


def english_pages():
    pages = [ROOT / name for name in FILES]
    for directory in DIRECTORIES:
        pages += [path for path in sorted((ROOT / directory).glob('*.md')) if not path.name.endswith('.ar.md')]
    return pages


def fenced_blocks(text):
    blocks, current = [], None
    for line in text.splitlines():
        if FENCE.match(line):
            if current is None:
                current = []
            else:
                blocks.append('\n'.join(current))
                current = None
        elif current is not None:
            current.append(line)
    return blocks


def switch_link(path):
    """The first line a page must carry: a link to the same page in the other language."""
    if path.name.endswith('.ar.md'):
        return f"[English]({path.name[:-len('.ar.md')]}.md)"
    return f'[العربية]({arabic_path(path).name})'


def first_line(path):
    return path.read_text(encoding='utf-8').splitlines()[0] if path.exists() else ''


class BilingualDocumentationTests(unittest.TestCase):
    def test_every_page_has_an_arabic_sibling(self):
        missing = [str(arabic_path(page).relative_to(ROOT)) for page in english_pages()
                   if page.exists() and not arabic_path(page).exists()]
        self.assertEqual(missing, [], 'reader-facing pages without an Arabic version')

    def test_every_arabic_page_has_an_english_page(self):
        orphans = [str(arabic.relative_to(ROOT)) for directory in DIRECTORIES
                   for arabic in sorted((ROOT / directory).glob('*.ar.md'))
                   if not arabic.with_name(arabic.name[:-len('.ar.md')] + '.md').exists()]
        self.assertEqual(orphans, [], 'Arabic pages whose English page is gone')

    def test_code_blocks_are_identical_in_both_languages(self):
        drift = [str(page.relative_to(ROOT)) for page in english_pages()
                 if arabic_path(page).exists() and fenced_blocks(page.read_text(encoding='utf-8'))
                 != fenced_blocks(arabic_path(page).read_text(encoding='utf-8'))]
        self.assertEqual(drift, [], 'pages whose Arabic code blocks differ from the English ones')

    def test_each_page_links_to_its_other_language(self):
        # English starts with [العربية](X.ar.md), Arabic with [English](X.md).
        unlinked = []
        for page in english_pages():
            arabic = arabic_path(page)
            if first_line(page).strip() != switch_link(page):
                unlinked.append(str(page.relative_to(ROOT)))
            if arabic.exists() and first_line(arabic).strip() != switch_link(arabic):
                unlinked.append(str(arabic.relative_to(ROOT)))
        self.assertEqual(unlinked, [], 'pages whose first line is not the link to the other language')


class CheckerSelfTests(unittest.TestCase):
    def test_fenced_blocks_are_extracted_in_order(self):
        text = 'a\n```bash\none\n```\nb\n~~~\ntwo\nthree\n~~~\n'
        self.assertEqual(fenced_blocks(text), ['one', 'two\nthree'])

    def test_switch_link_points_to_the_other_language(self):
        self.assertEqual(switch_link(DOCS / 'guides' / 'quickstart.md'), '[العربية](quickstart.ar.md)')
        self.assertEqual(switch_link(DOCS / 'guides' / 'quickstart.ar.md'), '[English](quickstart.md)')

    def test_arabic_path_keeps_the_directory(self):
        self.assertEqual(arabic_path(DOCS / 'guides' / 'quickstart.md'), DOCS / 'guides' / 'quickstart.ar.md')


if __name__ == '__main__':
    unittest.main()
