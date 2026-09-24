"""Guard: the app must not import stdlib modules that build.py excludes.

Nuitka builds with --nofollow-import-to=...,xml,... (and friends) to slim the
exe.  Importing such a module in main.py/src/ compiles and passes the test
suite (the module exists in the dev interpreter), then explodes at *runtime*
in the frozen binary - so the exclusion list in build.py is checked against
every import in the shipped sources.

Dynamic imports (importlib) are not visible to this scan; keep those imports
of excluded modules out of main.py/src/ by hand.
"""

import ast
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _excluded_modules() -> set[str]:
    """Module names from every --nofollow-import-to= flag in build.py."""
    with open(os.path.join(REPO_ROOT, "build.py"), encoding="utf-8") as f:
        source = f.read()
    excluded: set[str] = set()
    for flags in re.findall(r"--nofollow-import-to=([A-Za-z0-9_,]+)", source):
        excluded.update(flag.strip() for flag in flags.split(","))
    return excluded


def _imported_roots(paths: list[str]) -> set[str]:
    """Top-level module names imported anywhere in *paths* (AST scan)."""
    roots: set[str] = set()
    for path in paths:
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                roots.add(node.module.split(".")[0])
    return roots


def _shipped_sources() -> list[str]:
    paths = [os.path.join(REPO_ROOT, "main.py")]
    for dirpath, dirnames, filenames in os.walk(os.path.join(REPO_ROOT, "src")):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        paths.extend(os.path.join(dirpath, name) for name in filenames if name.endswith(".py"))
    return paths


class TestNoExcludedImports(unittest.TestCase):
    """main.py + src/ must not import anything Nuitka leaves out of the build."""

    def test_exclusion_list_is_detected(self) -> None:
        # Sanity check on the parser itself: a silently empty exclusion set
        # would make every test below pass vacuously.
        excluded = _excluded_modules()
        self.assertIn("xml", excluded)  # what actually bit us in the frozen exe

    def test_shipped_sources_avoid_excluded_modules(self) -> None:
        excluded = _excluded_modules()
        self.assertTrue(excluded, "build.py --nofollow-import-to flags not found")
        paths = _shipped_sources()
        self.assertGreater(len(paths), 5, "shipped source scan came up short")

        imported = _imported_roots(paths)
        offenders = sorted(imported & excluded)
        self.assertEqual(
            offenders,
            [],
            f"{offenders} are excluded by build.py --nofollow-import-to but imported "
            f"by shipped code - works in dev, ImportError in lpm.exe",
        )


if __name__ == "__main__":
    unittest.main()
