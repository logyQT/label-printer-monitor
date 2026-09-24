"""Tests for the data-root resolution in src/env.py.

The installed build keeps its writable state in a machine-wide location so a
headless Task Scheduler run resolves the same config/data/logs tree as the
interactive CLI, for every Windows account, without a loaded user profile.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import src.env as env

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FrozenDataRoot(unittest.TestCase):
    """_data_root() with a frozen (installed) build."""

    def test_defaults_to_programdata_app_dir(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("src.env._is_frozen", return_value=True),
            patch.dict(os.environ, {"ProgramData": tmp, "LPM_HOME": ""}),
        ):
            self.assertEqual(env.DATA_ROOT, os.path.join(tmp, "com.logy.lpm"))
            self.assertTrue(os.path.isdir(os.path.join(tmp, "com.logy.lpm")))

    def test_programdata_dir_is_created(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("src.env._is_frozen", return_value=True),
            patch.dict(os.environ, {"ProgramData": os.path.join(tmp, "ProgramData"), "LPM_HOME": ""}),
        ):
            target = os.path.join(tmp, "ProgramData", "com.logy.lpm")
            self.assertEqual(env.config_dir(), os.path.join(target, "config"))
            self.assertTrue(os.path.isdir(target))

    def test_lpm_home_overrides_programdata(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("src.env._is_frozen", return_value=True),
            patch.dict(os.environ, {"ProgramData": tmp, "LPM_HOME": os.path.join(tmp, "elsewhere")}),
        ):
            home = os.path.join(tmp, "elsewhere")
            self.assertEqual(env.DATA_ROOT, home)
            self.assertEqual(env.config_dir(), os.path.join(home, "config"))
            self.assertEqual(env.data_dir(), os.path.join(home, "data"))
            self.assertEqual(env.logs_dir(), os.path.join(home, "logs"))
            self.assertTrue(os.path.isdir(home))

    def test_blank_lpm_home_falls_back_to_programdata(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("src.env._is_frozen", return_value=True),
            patch.dict(os.environ, {"ProgramData": tmp, "LPM_HOME": "   "}),
        ):
            self.assertEqual(env.DATA_ROOT, os.path.join(tmp, "com.logy.lpm"))

    def test_missing_programdata_falls_back_to_wellknown_path(self) -> None:
        """A task launched with a stripped environment must not build a path
        from a stray relative root (the old %APPDATA% fallback did)."""
        with (
            patch("src.env._is_frozen", return_value=True),
            patch("src.env.os.makedirs"),
            patch.dict(os.environ, {}, clear=True),
        ):
            self.assertEqual(env.DATA_ROOT, os.path.join(r"C:\ProgramData", "com.logy.lpm"))

    def test_subdirs_hang_off_the_root(self) -> None:
        with (
            tempfile.TemporaryDirectory() as home,
            patch("src.env._is_frozen", return_value=True),
            patch.dict(os.environ, {"LPM_HOME": home}),
        ):
            self.assertEqual(env.backups_dir(), os.path.join(home, "data", "backups"))
            self.assertTrue(os.path.isdir(os.path.join(home, "data", "backups")))

    def test_bundled_config_ships_next_to_the_exe(self) -> None:
        # Read-only assets stay in the install dir, not the writable root.
        # The install dir comes from the loader, not from the sys.executable
        # lie - see ExePath below.
        with (
            patch("src.env._is_frozen", return_value=True),
            patch("src.env._module_filename", return_value=r"C:\Program Files\lpm\lpm.exe"),
        ):
            self.assertEqual(env.bundled_config_dir(), r"C:\Program Files\lpm\config")


class ExePath(unittest.TestCase):
    """exe_path(): the running binary, immune to the frozen-build lie."""

    def test_dev_mode_is_the_interpreter(self) -> None:
        with patch("src.env._is_frozen", return_value=False):
            self.assertEqual(env.exe_path(), os.path.abspath(sys.executable))

    def test_frozen_uses_the_loader_not_sys_executable(self) -> None:
        # Regression: Nuitka builds report sys.executable as
        # <install dir>\python.exe - a file that is never shipped.  The
        # scheduled task pinned it and Task Scheduler could not run it.
        with (
            patch("src.env._is_frozen", return_value=True),
            patch("src.env._module_filename", return_value=r"C:\Program Files\lpm\lpm.exe"),
            patch.object(sys, "executable", r"C:\Program Files\lpm\python.exe"),
        ):
            self.assertEqual(env.exe_path(), r"C:\Program Files\lpm\lpm.exe")

    def test_frozen_falls_back_when_the_loader_is_silent(self) -> None:
        with (
            patch("src.env._is_frozen", return_value=True),
            patch("src.env._module_filename", return_value=""),
        ):
            self.assertEqual(env.exe_path(), os.path.abspath(sys.executable))


class DevDataRoot(unittest.TestCase):
    """From-source runs stay in the repo tree, whatever the environment says."""

    def test_root_is_repo_root(self) -> None:
        with (
            patch("src.env._is_frozen", return_value=False),
            patch.dict(os.environ, {"LPM_HOME": "C:\\somewhere\\else"}),
        ):
            self.assertEqual(env.DATA_ROOT, REPO_ROOT)

    def test_bundled_config_is_the_repo_config_dir(self) -> None:
        with patch("src.env._is_frozen", return_value=False):
            self.assertEqual(env.bundled_config_dir(), os.path.join(REPO_ROOT, "config"))


if __name__ == "__main__":
    unittest.main()
