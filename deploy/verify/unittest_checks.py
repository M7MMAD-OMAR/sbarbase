"""Run built-in unittest without accepting skipped or empty discovery."""
import sys
import unittest


def main():
    suite = unittest.defaultTestLoader.discover("lab", pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.testsRun:
        print("FAIL: unittest discovered no tests", file=sys.stderr)
    if result.skipped:
        print("FAIL: skipped tests prevent portable verification acceptance", file=sys.stderr)
    return 0 if result.wasSuccessful() and result.testsRun and not result.skipped else 1


if __name__ == "__main__":
    raise SystemExit(main())
