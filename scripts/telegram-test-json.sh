#!/usr/bin/env bash
# Runs the tests of the Telegram sign-in change and the suites they belong to, then prints as the LAST stdout line only
#   {"total":N,"failed":N,"skipped":N}
# and exits nonzero when anything failed or no test ran. Everything else goes to stderr.
# TELEGRAM_FULL=1 runs every Bun test and every lab Python test instead of the named files.
# Heavy: run it on the second machine, `pc2 run bash scripts/telegram-test-json.sh`.
set -u
cd "$(dirname "$0")/.."
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
[ -d node_modules ] || bun install --frozen-lockfile >&2 || true
[ -d website/node_modules ] || bun install --cwd website --frozen-lockfile >&2 || true

bun_files=(tests/sign-in.test.ts tests/auth-config.test.ts tests/settings-validation.test.ts)
py_modules=(test_auth_settings test_auth_config_registry test_telegram_custom_provider test_docs_bilingual test_docs_links test_doc_references)
if [ "${TELEGRAM_FULL:-}" = 1 ]; then bun_args=(); else bun_args=("${bun_files[@]}"); fi

bun test "${bun_args[@]}" --reporter=junit --reporter-outfile="$work/bun.xml" >&2
bun_status=$?

if [ "${TELEGRAM_FULL:-}" = 1 ]; then py_args=(discover); else py_args=("${py_modules[@]}"); fi
(cd lab && /usr/bin/python3 - "$work/py.json" "${py_args[@]}" >&2 <<'PY'
import json, sys, unittest
out, args = sys.argv[1], sys.argv[2:]
loader = unittest.TestLoader()
suite = loader.discover('.', pattern='test_*.py') if args[:1] == ['discover'] else loader.loadTestsFromNames(args)
result = unittest.TextTestRunner(stream=sys.stderr, verbosity=1).run(suite)
bad = len(result.failures) + len(result.errors) + len(result.unexpectedSuccesses)
json.dump({'total': result.testsRun, 'failed': bad, 'skipped': len(result.skipped)}, open(out, 'w'))
PY
)
py_status=$?

/usr/bin/python3 - "$work/bun.xml" "$work/py.json" "$bun_status" "$py_status" <<'PY'
import json, os, sys
import xml.etree.ElementTree as ET
xml_path, py_path, bun_status, py_status = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
total = failed = skipped = 0
if os.path.exists(xml_path):
    # Count the leaf test cases, so nested suites are not counted twice.
    for case in ET.parse(xml_path).getroot().iter('testcase'):
        total += 1
        children = {child.tag for child in case}
        failed += bool(children & {'failure', 'error'})
        skipped += 'skipped' in children
if os.path.exists(py_path):
    data = json.load(open(py_path))
    total, failed, skipped = total + data['total'], failed + data['failed'], skipped + data['skipped']
# A runner that crashed without a failing case still fails the run.
if (bun_status or py_status) and not failed:
    failed = 1
print(json.dumps({'total': total, 'failed': failed, 'skipped': skipped}, separators=(',', ':')))
sys.exit(1 if failed or total == 0 else 0)
PY
