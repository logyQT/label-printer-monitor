"""Run all tests in _tests_/ directory.

Usage:
    python run_tests.py

Discovers all test_*.py files in _tests_/ and runs them with unittest.
Prints a summary and exits with code 0 (all pass) or 1 (failures/errors).
"""

import os
import sys
import unittest

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> None:
    """Discover and run all tests."""
    test_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tests_")

    if not os.path.isdir(test_dir):
        print(f"ERROR: Test directory not found: {test_dir}")
        sys.exit(2)

    loader = unittest.TestLoader()
    suite = loader.discover(test_dir, pattern="test_*.py", top_level_dir=os.path.dirname(test_dir))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    total = result.testsRun
    failed = len(result.failures)
    errors = len(result.errors)
    passed = total - failed - errors

    print(f"\n{'=' * 50}")
    print(f"Total: {total} | Passed: {passed} | Failed: {failed} | Errors: {errors}")
    print(f"{'=' * 50}")

    sys.exit(1 if (failed or errors) else 0)


if __name__ == "__main__":
    main()