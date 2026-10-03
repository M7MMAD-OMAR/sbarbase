"""Validate the durable goal, execution documents and comparison source record."""
import json
from pathlib import Path
import re


DOCUMENTS = (
    'PROJECT_GOAL.md',
    'COMPLIANCE.md',
    'docs/engineering/plans/README.md',
    'docs/engineering/plans/2026-10-03-product-and-portability-plan.md',
    'docs/engineering/plans/2026-10-03-gauntlet-execution-method.md',
    'docs/engineering/reviews/2026-10-03-docker-portability.md',
)


def check(root):
    """Return every planning-integrity error without changing the checkout."""
    root = Path(root).resolve()
    errors, texts = [], {}
    for name in DOCUMENTS:
        path = root / name
        try:
            text = path.read_text()
        except (OSError, UnicodeError) as error:
            errors.append(f'{name}: cannot read document ({type(error).__name__})')
            continue
        texts[name] = text
        if not text.endswith('\n'):
            errors.append(f'{name}: missing final newline')
        for number, line in enumerate(text.splitlines(), 1):
            if any(char in line for char in ('\u2013', '\u2014')):
                errors.append(f'{name}:{number}: prohibited dash')
            if line != line.rstrip():
                errors.append(f'{name}:{number}: trailing whitespace')
        for target in re.findall(r'\]\(([^)]+)\)', text):
            if '://' in target or target.startswith('#'):
                continue
            relative = target.split('#', 1)[0]
            linked = (path.parent / relative).resolve()
            if not linked.is_relative_to(root):
                errors.append(f'{name}: local link outside repository')
            elif not linked.exists():
                errors.append(f'{name}: missing local link {target}')

    plan = 'docs/engineering/plans/2026-10-03-product-and-portability-plan.md'
    for number in range(1, 12):
        if f'## {number}.' not in texts.get(plan, ''):
            errors.append(f'{plan}: missing section {number}')
    index = texts.get('docs/engineering/plans/README.md', '')
    for name in (Path(plan).name, '2026-10-03-gauntlet-execution-method.md'):
        if name not in index:
            errors.append(f'Plan index omits {name}')

    name = 'docs/engineering/gauntlet-ledger.json'
    try:
        ledger = json.loads((root / name).read_text())
        if not isinstance(ledger, dict) or ledger.get('schema') != 1:
            raise ValueError('invalid ledger schema')
        slices = ledger.get('slices')
        if not isinstance(slices, list) or not all(isinstance(item, dict) for item in slices):
            raise ValueError('invalid slice inventory')
        if sorted(item.get('id', '') for item in slices) != sorted(f'G{i}' for i in range(13)):
            errors.append(f'{name}: incomplete or duplicate slice inventory')
        for item in slices:
            if item.get('status') not in ('unproven', 'in-progress', 'passed', 'failed'):
                errors.append(f'{name}: invalid slice status')
            if not item.get('next') and item.get('status') != 'passed':
                errors.append(f'{name}: open slice has no next action')
            if item.get('status') == 'passed' and not item.get('evidence'):
                errors.append(f'{name}: passed slice has no evidence reference')
        if ledger.get('status') == 'complete' and any(item.get('status') != 'passed' for item in slices):
            errors.append(f'{name}: complete goal contains unproven slices')
    except (OSError, UnicodeError, ValueError, TypeError) as error:
        errors.append(f'{name}: invalid or missing ledger ({type(error).__name__})')

    name = 'docs/engineering/benchmarks/supabase-v0.8.2.source.json'
    try:
        reference = json.loads((root / name).read_text())
        if not isinstance(reference, dict) or reference.get('schema') != 1:
            raise ValueError('invalid source schema')
        if not re.fullmatch('[0-9a-f]{40}', reference.get('commit', '')):
            errors.append(f'{name}: unresolved full reference commit')
        files = reference.get('files')
        if not isinstance(files, list) or not files:
            raise ValueError('missing source files')
        for item in files:
            if not isinstance(item, dict) or not re.fullmatch('[0-9a-f]{64}', item.get('sha256', '')):
                errors.append(f'{name}: missing source checksum')
            elif reference['commit'] not in item.get('url', ''):
                errors.append(f'{name}: source URL is not bound to full commit')
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as error:
        errors.append(f'{name}: invalid or missing source identity ({type(error).__name__})')
    return errors


def main():
    errors = check(Path(__file__).resolve().parents[1])
    print('\n'.join(errors) if errors else 'PASS: planning integrity; runtime acceptance not established')
    return int(bool(errors))


if __name__ == '__main__':
    raise SystemExit(main())
