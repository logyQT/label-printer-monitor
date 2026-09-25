"""Tests for the lpm purge command (src/purge.py + main.py wiring).

Covers the flag-gated cleanup (nothing is ever deleted implicitly), the
safety refusals, idempotency and the exit codes the NSIS uninstaller
branches on.  All Task Scheduler access is mocked - the real Windows
Task Scheduler is never touched.
"""

import contextlib
import io
import os
import tempfile
import unittest
from unittest.mock import patch

from src.purge import run_purge
from src.schedule.meta import TASK_PATH


class PurgeTestCase(unittest.TestCase):
    """Shared fixture: a frozen build with LPM_HOME pointing at a temp tree.

    ``src.env._is_frozen`` is patched so LPM_HOME is honoured (dev mode
    deliberately ignores it and stays in the repo tree), and the tree gets
    one file per prunable subdirectory.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self._patches = [
            patch("src.env._is_frozen", return_value=True),
            patch.dict(os.environ, {"LPM_HOME": self.root}),
        ]
        for p in self._patches:
            p.start()
        for rel in ("config/config.json", "data/printer_stats.db", "data/backups/old.db", "logs/run.log"):
            path = os.path.join(self.root, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write("x\n")

    def tearDown(self) -> None:
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()

    def exists(self, *rel: str) -> bool:
        return os.path.exists(os.path.join(self.root, *rel))


class Usage(PurgeTestCase):
    """A bare `lpm purge` must never delete anything."""

    def test_no_flags_is_usage_error(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = run_purge()
        self.assertEqual(code, 2)
        self.assertIn("usage: lpm purge", err.getvalue())
        for rel in ("config", "data", "logs"):
            self.assertTrue(self.exists(rel), f"{rel} must survive a flagless purge")

    def test_dry_run_removes_nothing(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = run_purge(config=True, data=True, logs=True, dry_run=True)
        self.assertEqual(code, 0)
        for rel in ("config/config.json", "data/printer_stats.db", "logs/run.log"):
            self.assertTrue(self.exists(rel), rel)
        self.assertIn("would remove", out.getvalue())


class DirPruning(PurgeTestCase):
    """Each flag removes exactly its own subdirectory."""

    def test_config_only(self) -> None:
        self.assertEqual(run_purge(config=True), 0)
        self.assertFalse(self.exists("config"))
        self.assertTrue(self.exists("data/printer_stats.db"))
        self.assertTrue(self.exists("logs/run.log"))

    def test_data_only(self) -> None:
        self.assertEqual(run_purge(data=True), 0)
        self.assertFalse(self.exists("data"))
        self.assertTrue(self.exists("config/config.json"))
        self.assertTrue(self.exists("logs/run.log"))

    def test_logs_only(self) -> None:
        self.assertEqual(run_purge(logs=True), 0)
        self.assertFalse(self.exists("logs"))
        self.assertTrue(self.exists("config/config.json"))
        self.assertTrue(self.exists("data/printer_stats.db"))

    def test_all_prunes_and_drops_the_emptied_root(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = run_purge(config=True, data=True, logs=True)
        self.assertEqual(code, 0)
        for rel in ("config", "data", "logs"):
            self.assertFalse(self.exists(rel), rel)
        # The root itself only goes when the prunes emptied it.
        self.assertFalse(os.path.isdir(self.root))
        self.assertIn("removed (empty)", out.getvalue())

    def test_root_survives_unrelated_files(self) -> None:
        stray = os.path.join(self.root, "operator-notes.txt")
        with open(stray, "w", encoding="utf-8") as f:
            f.write("keep\n")
        self.assertEqual(run_purge(config=True, data=True, logs=True), 0)
        self.assertFalse(self.exists("config"))
        self.assertTrue(os.path.isfile(stray))

    def test_missing_targets_are_a_noop(self) -> None:
        # Uninstalling twice, or a machine that never ran `lpm init`.
        self.assertEqual(run_purge(config=True, data=True, logs=True), 0)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = run_purge(config=True, data=True, logs=True)
        self.assertEqual(code, 0)
        self.assertIn("not present", out.getvalue())

    def test_purge_twice_is_idempotent(self) -> None:
        self.assertEqual(run_purge(config=True, data=True, logs=True), 0)
        self.assertEqual(run_purge(config=True, data=True, logs=True), 0)


class SafetyRefusals(PurgeTestCase):
    """A catastrophic data root is refused, not rmtree'd."""

    def _refused(self, root: str) -> int:
        err = io.StringIO()
        with (
            patch("src.purge.data_root", return_value=root),
            contextlib.redirect_stderr(err),
        ):
            code = run_purge(config=True, data=True, logs=True)
        self.assertEqual(code, 1, err.getvalue())
        self.assertIn("refusing to purge data root", err.getvalue())
        return code

    def test_refuses_drive_root(self) -> None:
        self._refused("C:\\")

    def test_refuses_windows_dir(self) -> None:
        windows = os.environ.get("WINDIR", r"C:\Windows")
        self._refused(windows)
        self._refused(os.path.join(windows, "System32"))

    def test_refuses_empty_path(self) -> None:
        self._refused("   ")

    def test_refuses_the_install_dir(self) -> None:
        install_dir = os.path.join(self.root, "app")
        os.makedirs(install_dir, exist_ok=True)
        with (
            patch("src.purge.data_root", return_value=install_dir),
            patch("src.purge.exe_path", return_value=os.path.join(install_dir, "lpm.exe")),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(run_purge(logs=True), 1)
        self.assertTrue(self.exists("logs/run.log"), "install dir contents must survive")

    def test_refusal_does_not_strand_other_targets(self) -> None:
        # The task prune runs before the refusal - it must not be rolled back
        # (it cannot be) and the exit code still reports the failure.
        with (
            patch("src.purge.data_root", return_value="C:\\"),
            patch("src.purge.is_admin", return_value=False),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(run_purge(tasks=True, config=True), 1)


class TaskPruning(PurgeTestCase):
    """`lpm purge --tasks`: elevation gate, COM errors, idempotency."""

    def test_without_admin_fails_and_never_touches_the_scheduler(self) -> None:
        err = io.StringIO()
        with (
            patch("src.purge.is_admin", return_value=False),
            patch("src.purge.connect") as connect,
            contextlib.redirect_stderr(err),
        ):
            code = run_purge(tasks=True)
        self.assertEqual(code, 1)
        self.assertIn("elevated session", err.getvalue())
        connect.assert_not_called()

    def test_removes_the_task(self) -> None:
        scheduler = object()
        out = io.StringIO()
        with (
            patch("src.purge.is_admin", return_value=True),
            patch("src.purge.connect", return_value=scheduler),
            patch("src.purge.delete_task", return_value=True) as delete_task,
            contextlib.redirect_stdout(out),
        ):
            code = run_purge(tasks=True)
        self.assertEqual(code, 0)
        delete_task.assert_called_once_with(scheduler, verbose=True)
        self.assertIn(TASK_PATH, out.getvalue())

    def test_absent_task_is_success(self) -> None:
        # Idempotent: no task registered counts as "nothing left to do".
        with (
            patch("src.purge.is_admin", return_value=True),
            patch("src.purge.connect", return_value=object()),
            patch("src.purge.delete_task", return_value=False),
        ):
            self.assertEqual(run_purge(tasks=True), 0)

    def test_com_error_fails(self) -> None:
        err = io.StringIO()
        with (
            patch("src.purge.is_admin", return_value=True),
            patch("src.purge.connect", side_effect=Exception("RPC server unavailable")),
            contextlib.redirect_stderr(err),
        ):
            code = run_purge(tasks=True)
        self.assertEqual(code, 1)
        self.assertIn("Task Scheduler", err.getvalue())

    def test_dry_run_never_touches_the_scheduler(self) -> None:
        with (
            patch("src.purge.is_admin", return_value=True) as is_admin,
            patch("src.purge.connect") as connect,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            code = run_purge(tasks=True, dry_run=True)
        self.assertEqual(code, 0)
        connect.assert_not_called()
        is_admin.assert_not_called()

    def test_failed_task_does_not_strand_the_dir_prunes(self) -> None:
        out = io.StringIO()
        with (
            patch("src.purge.is_admin", return_value=False),
            patch("src.purge.connect") as connect,
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            code = run_purge(tasks=True, config=True)
        self.assertEqual(code, 1)
        connect.assert_not_called()
        self.assertFalse(self.exists("config"), "the config prune must still run")
        self.assertTrue(self.exists("logs/run.log"))


if __name__ == "__main__":
    unittest.main()
