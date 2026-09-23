"""Tests for the --schedule feature (src/schedule + main.py wiring).

All COM access is mocked - the real Windows Task Scheduler is never touched.
Covers the plan's Testing table: config validation, task XML building,
install/update/up-to-date/remove, and the --validate schedule check.
"""

import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import main
from src.schedule import check, install, remove
from src.schedule.commands import build_task_xml, folder_exists
from src.schedule.meta import TASK_FOLDER
from src.schedule.validate import check_schedule, validate_schedule_config
from src.validate import FAIL, OK, WARN, Issue

VALID_SCHEDULE: dict = {
    "enabled": True,
    "times": ["05:00", "15:00"],
    "weekdays_only": True,
}


def configure_dispatch(dispatch: MagicMock, task_xml: str | None) -> None:
    """Wire a Dispatch mock to a stateful \\LPM task folder.

    The folder (and task) exists iff *task_xml* is not None; calling
    root.CreateFolder("LPM") makes it appear, mirroring what register_task()
    expects from the real scheduler.
    """
    scheduler = dispatch.return_value
    state = {"exists": task_xml is not None}
    lpm_folder = MagicMock(name="LPM_Folder")
    root_folder = MagicMock(name="Root_Folder")
    root_folder.CreateFolder.side_effect = lambda _name: state.update(exists=True)
    if task_xml is not None:
        lpm_folder.GetTask.return_value.Xml = task_xml

    def get_folder(p: str) -> MagicMock:
        if p == "\\":
            return root_folder
        if state["exists"] and p == TASK_FOLDER:
            return lpm_folder
        raise Exception(f"folder not found: {p}")

    scheduler.GetFolder.side_effect = get_folder


def write_config(directory: str, data: dict) -> str:
    path = os.path.join(directory, "config.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return path


class TestBuildTaskXml(unittest.TestCase):
    """build_task_xml() trigger and structure tests."""

    def test_multi_time_triggers(self) -> None:
        xml = build_task_xml(["05:00", "15:00"], True)
        self.assertEqual(xml.count("<CalendarTrigger>"), 2)
        self.assertIn("<StartBoundary>2026-01-01T05:00:00</StartBoundary>", xml)
        self.assertIn("<StartBoundary>2026-01-01T15:00:00</StartBoundary>", xml)

        single = build_task_xml(["07:30"], True)
        self.assertEqual(single.count("<CalendarTrigger>"), 1)
        self.assertIn("T07:30:00", single)

    def test_weekdays_only_flag(self) -> None:
        weekdays = build_task_xml(["05:00"], True)
        self.assertIn("<DaysOfWeek>", weekdays)
        self.assertIn("<ScheduleByWeek>", weekdays)
        self.assertNotIn("<ScheduleByDay>", weekdays)
        self.assertIn("<Monday/>", weekdays)
        self.assertIn("<Friday/>", weekdays)

        daily = build_task_xml(["05:00"], False)
        self.assertNotIn("<DaysOfWeek>", daily)
        self.assertNotIn("<ScheduleByWeek>", daily)
        self.assertIn("<ScheduleByDay>", daily)
        self.assertIn("<DaysInterval>1</DaysInterval>", daily)

    def test_action_and_settings(self) -> None:
        xml = build_task_xml(["05:00"], True)
        self.assertIn("<Command>lpm</Command>", xml)
        self.assertIn("<Arguments>--collect</Arguments>", xml)
        self.assertIn("<WakeToRun>true</WakeToRun>", xml)
        self.assertIn("<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>", xml)
        self.assertIn("<LogonType>S4U</LogonType>", xml)
        self.assertNotIn("InteractiveToken", xml)
        self.assertIn("<RunLevel>LeastPrivilege</RunLevel>", xml)


class TestScheduleConfigValidation(unittest.TestCase):
    """validate_schedule_config() tests."""

    def test_validate_times_format(self) -> None:
        valid = {"schedule": {"enabled": True, "times": ["00:00", "05:00", "09:05", "23:59"]}}
        self.assertEqual(validate_schedule_config(valid), [])

        for bad in ["5:00", "25:00", "05:0", "abc", "05:00:00", ""]:
            config = {"schedule": {"enabled": True, "times": [bad]}}
            errors = validate_schedule_config(config)
            self.assertTrue(errors, f"expected an error for time {bad!r}")

        non_string = {"schedule": {"enabled": True, "times": [5]}}
        self.assertTrue(validate_schedule_config(non_string))

    def test_validate_times_empty(self) -> None:
        empty = {"schedule": {"enabled": True, "times": []}}
        self.assertTrue(validate_schedule_config(empty))

        missing = {"schedule": {"enabled": True}}
        self.assertTrue(validate_schedule_config(missing))

        not_a_list = {"schedule": {"enabled": True, "times": "05:00"}}
        self.assertTrue(validate_schedule_config(not_a_list))

    def test_validate_weekdays_only_type(self) -> None:
        bad = {"schedule": {"enabled": True, "times": ["05:00"], "weekdays_only": "yes"}}
        self.assertTrue(validate_schedule_config(bad))

        absent = {"schedule": {"enabled": True, "times": ["05:00"]}}
        self.assertEqual(validate_schedule_config(absent), [])

        explicit = {"schedule": {"enabled": True, "times": ["05:00"], "weekdays_only": False}}
        self.assertEqual(validate_schedule_config(explicit), [])

    def test_validate_enabled_type(self) -> None:
        missing = {"schedule": {"times": ["05:00"]}}
        self.assertTrue(validate_schedule_config(missing))

        string = {"schedule": {"enabled": "true", "times": ["05:00"]}}
        self.assertTrue(validate_schedule_config(string))

        no_section: dict = {}
        self.assertTrue(validate_schedule_config(no_section))

    def test_check_alias(self) -> None:
        config = {"schedule": {"enabled": True, "times": ["05:00"]}}
        self.assertEqual(check(config), validate_schedule_config(config))


class TestScheduleHandler(unittest.TestCase):
    """_handle_schedule() gates: admin (first), FROZEN, PATH, config, disabled."""

    def test_schedule_requires_admin(self) -> None:
        """Admin gate fires first, before any other message (e.g. FROZEN)."""
        err = io.StringIO()
        with (
            patch("main._is_admin", return_value=False),
            patch("main.FROZEN", False),  # would print the FROZEN error if reached
            contextlib.redirect_stderr(err),
            self.assertRaises(SystemExit) as ctx,
        ):
            main._handle_schedule(argparse.Namespace(remove=False))
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("ERROR: --schedule requires an elevated session", err.getvalue())
        self.assertNotIn("requires a built version", err.getvalue())
        # End users don't need the internals (S4U, re-run hints) - just the ask.
        self.assertNotIn("S4U", err.getvalue())

    def test_schedule_not_frozen(self) -> None:
        err = io.StringIO()
        with (
            patch("main._is_admin", return_value=True),
            patch("main.FROZEN", False),
            contextlib.redirect_stderr(err),
            self.assertRaises(SystemExit) as ctx,
        ):
            main._handle_schedule(argparse.Namespace(remove=False))
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("ERROR: --schedule requires a built version (lpm.exe).", err.getvalue())
        self.assertIn("Build with build.py first", err.getvalue())

    def test_schedule_lpm_not_on_path(self) -> None:
        err = io.StringIO()
        with (
            patch("main._is_admin", return_value=True),
            patch("main.FROZEN", True),
            patch("main.shutil.which", return_value=None),
            contextlib.redirect_stderr(err),
            self.assertRaises(SystemExit) as ctx,
        ):
            main._handle_schedule(argparse.Namespace(remove=False))
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("ERROR: 'lpm' not found on PATH.", err.getvalue())

    def test_schedule_no_config_section(self) -> None:
        err = io.StringIO()
        with (
            patch("main._is_admin", return_value=True),
            patch("main.FROZEN", True),
            patch("main.shutil.which", return_value="C:\\bin\\lpm.exe"),
            patch("main.load_config", return_value={}),
            contextlib.redirect_stderr(err),
            self.assertRaises(SystemExit) as ctx,
        ):
            main._handle_schedule(argparse.Namespace(remove=False))
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("ERROR: No 'schedule' section in config.json.", err.getvalue())
        self.assertIn('"schedule": {', err.getvalue())

    def test_schedule_disabled(self) -> None:
        config = {"schedule": {"enabled": False, "times": ["05:00"]}}
        out = io.StringIO()
        with (
            patch("main._is_admin", return_value=True),
            patch("main.FROZEN", True),
            patch("main.shutil.which", return_value="C:\\bin\\lpm.exe"),
            patch("main.load_config", return_value=config),
            patch("src.schedule.commands.win32com.client.Dispatch") as mock_dispatch,
            contextlib.redirect_stdout(out),
        ):
            main._handle_schedule(argparse.Namespace(remove=False))
        self.assertIn("Schedule is disabled in config.", out.getvalue())
        mock_dispatch.assert_not_called()

    def test_schedule_flag_dispatch(self) -> None:
        """main() parses --schedule and routes it to the handler."""
        err = io.StringIO()
        with (
            patch("sys.argv", ["lpm", "--schedule"]),
            patch("main._is_admin", return_value=True),
            patch("main.FROZEN", False),
            contextlib.redirect_stderr(err),
            self.assertRaises(SystemExit) as ctx,
        ):
            main.main()
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("requires a built version", err.getvalue())

    @patch("src.schedule.commands.win32com.client.Dispatch")
    def test_schedule_creates_via_handler(self, mock_dispatch: MagicMock) -> None:
        configure_dispatch(mock_dispatch, task_xml=None)
        config = {"schedule": dict(VALID_SCHEDULE)}
        out = io.StringIO()
        with (
            patch("main._is_admin", return_value=True),
            patch("main.FROZEN", True),
            patch("main.shutil.which", return_value="C:\\bin\\lpm.exe"),
            patch("main.load_config", return_value=config),
            patch("src.schedule.commands.win32com.client.Dispatch", mock_dispatch),
            contextlib.redirect_stdout(out),
        ):
            main._handle_schedule(argparse.Namespace(remove=False))
        self.assertIn("Schedule created", out.getvalue())


class TestInstallRemove(unittest.TestCase):
    """install() and remove() against a mocked Task Scheduler."""

    @patch("src.schedule.commands.win32com.client.Dispatch")
    def test_task_creation(self, mock_dispatch: MagicMock) -> None:
        configure_dispatch(mock_dispatch, task_xml=None)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            install({"schedule": dict(VALID_SCHEDULE)})
        self.assertIn("Schedule created", out.getvalue())

        scheduler = mock_dispatch.return_value
        root = scheduler.GetFolder("\\")
        root.CreateFolder.assert_called_once_with("LPM")
        lpm = scheduler.GetFolder(TASK_FOLDER)
        self.assertEqual(lpm.RegisterTask.call_count, 1)
        call = lpm.RegisterTask.call_args
        self.assertEqual(call.args[0], "LPM_Collect")
        self.assertEqual(call.args[2], 0)  # TASK_CREATE_OR_UPDATE
        self.assertEqual(call.args[5], 2)  # TASK_LOGON_S4U
        xml = call.args[1]
        self.assertEqual(xml.count("<CalendarTrigger>"), 2)
        self.assertIn("<StartBoundary>2026-01-01T05:00:00</StartBoundary>", xml)
        self.assertIn("<StartBoundary>2026-01-01T15:00:00</StartBoundary>", xml)

    @patch("src.schedule.commands.win32com.client.Dispatch")
    def test_task_update(self, mock_dispatch: MagicMock) -> None:
        configure_dispatch(mock_dispatch, task_xml=build_task_xml(["09:00"], False))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            install({"schedule": dict(VALID_SCHEDULE)})
        self.assertIn("Schedule updated", out.getvalue())

        scheduler = mock_dispatch.return_value
        scheduler.GetFolder("\\").CreateFolder.assert_not_called()
        lpm = scheduler.GetFolder(TASK_FOLDER)
        self.assertEqual(lpm.RegisterTask.call_count, 1)

    @patch("src.schedule.commands.win32com.client.Dispatch")
    def test_task_up_to_date(self, mock_dispatch: MagicMock) -> None:
        configure_dispatch(mock_dispatch, task_xml=build_task_xml(["05:00", "15:00"], True))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            install({"schedule": dict(VALID_SCHEDULE)})
        self.assertIn("Schedule is up to date.", out.getvalue())

        scheduler = mock_dispatch.return_value
        lpm = scheduler.GetFolder(TASK_FOLDER)
        lpm.RegisterTask.assert_not_called()

    @patch("src.schedule.commands.win32com.client.Dispatch")
    def test_task_removal(self, mock_dispatch: MagicMock) -> None:
        configure_dispatch(mock_dispatch, task_xml="<Task/>")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            remove()
        self.assertIn("Schedule removed.", out.getvalue())

        scheduler = mock_dispatch.return_value
        lpm = scheduler.GetFolder(TASK_FOLDER)
        lpm.DeleteTask.assert_called_once_with("LPM_Collect", 0)
        root = scheduler.GetFolder("\\")
        root.DeleteFolder.assert_called_once_with("LPM", 0)

    @patch("src.schedule.commands.win32com.client.Dispatch")
    def test_task_removal_no_tasks(self, mock_dispatch: MagicMock) -> None:
        configure_dispatch(mock_dispatch, task_xml=None)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            remove()
        self.assertIn("No LPM scheduled tasks found.", out.getvalue())

        scheduler = mock_dispatch.return_value
        paths = [c.args[0] for c in scheduler.GetFolder.call_args_list]
        self.assertNotIn("\\", paths)

    @patch("src.schedule.commands.win32com.client.Dispatch")
    def test_task_update_logon_type(self, mock_dispatch: MagicMock) -> None:
        # Task registered before the S4U change: triggers match, principal doesn't.
        old_xml = build_task_xml(["05:00", "15:00"], True).replace("S4U", "InteractiveToken")
        configure_dispatch(mock_dispatch, task_xml=old_xml)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            install({"schedule": dict(VALID_SCHEDULE)})
        self.assertIn("Schedule updated", out.getvalue())
        scheduler = mock_dispatch.return_value
        self.assertEqual(scheduler.GetFolder(TASK_FOLDER).RegisterTask.call_count, 1)

    @patch("src.schedule.commands.win32com.client.Dispatch")
    def test_register_falls_back_to_delete_recreate(self, mock_dispatch: MagicMock) -> None:
        # Existing task differs (so register runs); principal change rejected
        # on update -> delete + recreate (plan flow).
        configure_dispatch(mock_dispatch, task_xml=build_task_xml(["09:00"], False))
        scheduler = mock_dispatch.return_value
        lpm = scheduler.GetFolder(TASK_FOLDER)
        lpm.RegisterTask.side_effect = [Exception("access denied"), None]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            install({"schedule": dict(VALID_SCHEDULE)})
        self.assertIn("Schedule updated", out.getvalue())
        lpm.DeleteTask.assert_called_once_with("LPM_Collect", 0)
        self.assertEqual(lpm.RegisterTask.call_count, 2)

    @patch("src.schedule.commands.win32com.client.Dispatch")
    def test_register_fails_loudly_when_both_attempts_fail(self, mock_dispatch: MagicMock) -> None:
        class RegisterError(Exception):
            """Stand-in for pywintypes.com_error (assertable without B017)."""

        configure_dispatch(mock_dispatch, task_xml=build_task_xml(["09:00"], False))
        scheduler = mock_dispatch.return_value
        lpm = scheduler.GetFolder(TASK_FOLDER)
        lpm.RegisterTask.side_effect = RegisterError("broken xml")
        with self.assertRaises(RegisterError):
            install({"schedule": dict(VALID_SCHEDULE)})

    @patch("src.schedule.commands.win32com.client.Dispatch")
    def test_install_rejects_bad_times(self, mock_dispatch: MagicMock) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
            install({"schedule": {"enabled": True, "times": ["bogus"]}})
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("Invalid time", err.getvalue())
        mock_dispatch.assert_not_called()

    @patch("src.schedule.commands.win32com.client.Dispatch")
    def test_install_requires_schedule_section(self, mock_dispatch: MagicMock) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
            install({})
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("No 'schedule' section", err.getvalue())
        mock_dispatch.assert_not_called()

    @patch("src.schedule.commands.win32com.client.Dispatch")
    def test_folder_exists(self, mock_dispatch: MagicMock) -> None:
        configure_dispatch(mock_dispatch, task_xml="<Task/>")
        scheduler = mock_dispatch.return_value
        self.assertTrue(folder_exists(scheduler, TASK_FOLDER))
        self.assertFalse(folder_exists(scheduler, "\\Other"))


class TestCheckSchedule(unittest.TestCase):
    """check_schedule() - the --validate scheduled-task check (Cases 1 & 2)."""

    @patch("src.schedule.commands.win32com.client.Dispatch")
    def test_validate_stray_task(self, mock_dispatch: MagicMock) -> None:
        configure_dispatch(mock_dispatch, task_xml="<Task/>")
        with tempfile.TemporaryDirectory() as tmp:
            path = write_config(tmp, {"printers": []})
            issues = check_schedule(path, tmp)
        self.assertEqual(len(issues), 1)
        self.assertEqual(
            issues[0],
            Issue(
                WARN,
                "Stray scheduled task found: \\LPM\\LPM_Collect. Run lpm --schedule --remove to clean it up.",
            ),
        )

    @patch("src.schedule.commands.win32com.client.Dispatch")
    def test_validate_no_task_no_config(self, mock_dispatch: MagicMock) -> None:
        configure_dispatch(mock_dispatch, task_xml=None)
        with tempfile.TemporaryDirectory() as tmp:
            path = write_config(tmp, {"printers": []})
            issues = check_schedule(path, tmp)
        self.assertEqual(issues, [Issue(OK, "No scheduled tasks configured")])

    @patch("src.schedule.commands.win32com.client.Dispatch")
    def test_validate_disabled_no_task(self, mock_dispatch: MagicMock) -> None:
        configure_dispatch(mock_dispatch, task_xml=None)
        with tempfile.TemporaryDirectory() as tmp:
            path = write_config(tmp, {"schedule": {"enabled": False, "times": ["05:00"]}})
            issues = check_schedule(path, tmp)
        self.assertEqual(issues, [Issue(OK, "Scheduled collection disabled in config")])

    @patch("src.schedule.commands.win32com.client.Dispatch")
    def test_validate_disabled_with_task(self, mock_dispatch: MagicMock) -> None:
        configure_dispatch(mock_dispatch, task_xml=build_task_xml(["05:00"], True))
        with tempfile.TemporaryDirectory() as tmp:
            path = write_config(tmp, {"schedule": {"enabled": False, "times": ["05:00"]}})
            issues = check_schedule(path, tmp)
        expected = "Stray scheduled task found: \\LPM\\LPM_Collect. Run lpm --schedule --remove to clean it up."
        self.assertEqual(issues, [Issue(WARN, expected)])

    @patch("src.schedule.commands.win32com.client.Dispatch")
    def test_validate_no_task_with_config(self, mock_dispatch: MagicMock) -> None:
        configure_dispatch(mock_dispatch, task_xml=None)
        with tempfile.TemporaryDirectory() as tmp:
            path = write_config(tmp, {"schedule": dict(VALID_SCHEDULE)})
            issues = check_schedule(path, tmp)
        self.assertEqual(
            issues,
            [Issue(WARN, "No scheduled task found. Run lpm --schedule to create one.")],
        )

    @patch("src.schedule.commands.win32com.client.Dispatch")
    def test_validate_matches(self, mock_dispatch: MagicMock) -> None:
        configure_dispatch(mock_dispatch, task_xml=build_task_xml(["15:00", "05:00"], True))
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("src.schedule.validate.shutil.which", return_value="C:\\bin\\lpm.exe"),
        ):
            path = write_config(tmp, {"schedule": dict(VALID_SCHEDULE)})
            issues = check_schedule(path, tmp)
        self.assertEqual(issues, [Issue(OK, "Scheduled task matches config")])

    @patch("src.schedule.commands.win32com.client.Dispatch")
    def test_validate_differs(self, mock_dispatch: MagicMock) -> None:
        configure_dispatch(mock_dispatch, task_xml=build_task_xml(["09:00"], True))
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("src.schedule.validate.shutil.which", return_value="C:\\bin\\lpm.exe"),
        ):
            path = write_config(tmp, {"schedule": dict(VALID_SCHEDULE)})
            issues = check_schedule(path, tmp)
        self.assertEqual(
            issues,
            [Issue(WARN, "Scheduled task differs from config. Run lpm --schedule to update.")],
        )

    @patch("src.schedule.commands.win32com.client.Dispatch")
    def test_validate_differs_on_weekdays_flag(self, mock_dispatch: MagicMock) -> None:
        configure_dispatch(mock_dispatch, task_xml=build_task_xml(["05:00", "15:00"], False))
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("src.schedule.validate.shutil.which", return_value="C:\\bin\\lpm.exe"),
        ):
            path = write_config(tmp, {"schedule": dict(VALID_SCHEDULE)})
            issues = check_schedule(path, tmp)
        self.assertEqual(issues[0].level, WARN)

    @patch("src.schedule.commands.win32com.client.Dispatch")
    def test_validate_differs_on_logon_type(self, mock_dispatch: MagicMock) -> None:
        old_xml = build_task_xml(["05:00", "15:00"], True).replace("S4U", "InteractiveToken")
        configure_dispatch(mock_dispatch, task_xml=old_xml)
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("src.schedule.validate.shutil.which", return_value="C:\\bin\\lpm.exe"),
        ):
            path = write_config(tmp, {"schedule": dict(VALID_SCHEDULE)})
            issues = check_schedule(path, tmp)
        self.assertEqual(
            issues,
            [Issue(WARN, "Scheduled task differs from config. Run lpm --schedule to update.")],
        )

    @patch("src.schedule.commands.win32com.client.Dispatch")
    def test_validate_orphaned_exe(self, mock_dispatch: MagicMock) -> None:
        configure_dispatch(mock_dispatch, task_xml=build_task_xml(["05:00", "15:00"], True))
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("src.schedule.validate.shutil.which", return_value=None),
        ):
            path = write_config(tmp, {"schedule": dict(VALID_SCHEDULE)})
            issues = check_schedule(path, tmp)
        self.assertEqual(
            issues,
            [Issue(FAIL, "Scheduled task points to missing executable")],
        )

    @patch("src.schedule.commands.win32com.client.Dispatch")
    def test_validate_com_unavailable(self, mock_dispatch: MagicMock) -> None:
        mock_dispatch.side_effect = Exception("RPC server unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            path = write_config(tmp, {"schedule": dict(VALID_SCHEDULE)})
            issues = check_schedule(path, tmp)
        self.assertEqual(
            issues,
            [Issue(WARN, "Task Scheduler not available: RPC server unavailable")],
        )


class TestSchemaGuard(unittest.TestCase):
    """config/config.json.schema is the single schema source (vendored into builds).

    It must keep accepting the schedule section: before vendoring, packaged
    installs failed with "Additional properties are not allowed ('schedule'
    was unexpected)" against a stale copy.
    """

    def _repo_schema(self) -> dict:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(repo_root, "config", "config.json.schema")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def test_schema_validates_schedule_config(self) -> None:
        from src.validate import find_schema_violation

        config = {
            "db": {"filename": "printer_stats.db"},
            "log_dir": "logs",
            "snmp": {"community": "public", "timeout_sec": 3, "retries": 2},
            "printers": [{"ip": "10.0.1.10", "model": "Zebra ZT411", "location": "Line 1"}],
            "schedule": dict(VALID_SCHEDULE),
        }
        self.assertIsNone(find_schema_violation(config, self._repo_schema()))

        bad_times = {**config, "schedule": {"enabled": True, "times": ["bogus"]}}
        self.assertIsNotNone(find_schema_violation(bad_times, self._repo_schema()))

    def test_example_config_valid_and_schedule_disabled(self) -> None:
        from src.validate import find_schema_violation

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(repo_root, "config", "config.example.json")
        with open(path, encoding="utf-8") as f:
            example = json.load(f)
        self.assertIsNone(find_schema_violation(example, self._repo_schema()))
        self.assertIs(example["schedule"]["enabled"], False)


class TestSchemaRefresh(unittest.TestCase):
    """_refresh_schema_copy() replaces the stale %APPDATA% copy on --validate."""

    def _write(self, path: str, data: bytes) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)

    def test_replaces_stale_copy(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            src_dir = os.path.join(root, "shipped")
            src = os.path.join(src_dir, "config.json.schema")
            dst = os.path.join(root, "appdata", "config.json.schema")
            self._write(src, b'{"new": true}')
            self._write(dst, b'{"old": true}')
            with (
                patch("main.bundled_config_dir", return_value=src_dir),
                patch("main._schema_path", return_value=dst),
            ):
                replaced = main._refresh_schema_copy()
            self.assertTrue(replaced)
            with open(dst, "rb") as f:
                self.assertEqual(f.read(), b'{"new": true}')

    def test_noop_when_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            src_dir = os.path.join(root, "shipped")
            src = os.path.join(src_dir, "config.json.schema")
            dst = os.path.join(root, "appdata", "config.json.schema")
            self._write(src, b'{"same": true}')
            self._write(dst, b'{"same": true}')
            with (
                patch("main.bundled_config_dir", return_value=src_dir),
                patch("main._schema_path", return_value=dst),
            ):
                self.assertFalse(main._refresh_schema_copy())

    def test_noop_when_same_path(self) -> None:
        # dev mode: config_dir IS the bundled dir - must never self-copy
        with tempfile.TemporaryDirectory() as src_dir:
            path = os.path.join(src_dir, "config.json.schema")
            self._write(path, b'{"dev": true}')
            with (
                patch("main.bundled_config_dir", return_value=src_dir),
                patch("main._schema_path", return_value=path),
            ):
                self.assertFalse(main._refresh_schema_copy())

    def test_noop_when_source_missing(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            dst = os.path.join(root, "appdata", "config.json.schema")
            self._write(dst, b'{"keep": true}')
            with (
                patch("main.bundled_config_dir", return_value=os.path.join(root, "absent")),
                patch("main._schema_path", return_value=dst),
            ):
                self.assertFalse(main._refresh_schema_copy())
            with open(dst, "rb") as f:
                self.assertEqual(f.read(), b'{"keep": true}')

    def test_creates_missing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            src_dir = os.path.join(root, "shipped")
            src = os.path.join(src_dir, "config.json.schema")
            dst = os.path.join(root, "nested", "deep", "config.json.schema")
            self._write(src, b'{"fresh": true}')
            with (
                patch("main.bundled_config_dir", return_value=src_dir),
                patch("main._schema_path", return_value=dst),
            ):
                self.assertTrue(main._refresh_schema_copy())
            self.assertTrue(os.path.exists(dst))


class TestInitVendored(unittest.TestCase):
    """--init copies the shipped example/schema (embedded strings are gone)."""

    def _write_source(self, src_dir: str) -> None:
        os.makedirs(src_dir, exist_ok=True)
        with open(os.path.join(src_dir, "config.example.json"), "w", encoding="utf-8") as f:
            f.write('{"printers": []}')
        with open(os.path.join(src_dir, "config.json.schema"), "w", encoding="utf-8") as f:
            f.write('{"type": "object"}')

    @patch("main.FROZEN", True)
    def test_init_copies_shipped_files(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            src_dir = os.path.join(root, "shipped")
            cfg_dir = os.path.join(root, "config")
            self._write_source(src_dir)
            out = io.StringIO()
            with (
                patch("main.bundled_config_dir", return_value=src_dir),
                patch("main.config_dir", return_value=cfg_dir),
                patch("main.data_dir", return_value=os.path.join(root, "data")),
                patch("main.logs_dir", return_value=os.path.join(root, "logs")),
                patch("main.backups_dir", return_value=os.path.join(root, "backups")),
                contextlib.redirect_stdout(out),
            ):
                main._handle_init()
            target = os.path.join(cfg_dir, "config.json")
            self.assertTrue(os.path.exists(target))
            with open(target, encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"printers": []})
            self.assertTrue(os.path.exists(os.path.join(cfg_dir, "config.json.schema")))
            self.assertTrue(os.path.exists(os.path.join(cfg_dir, "config.example.json")))
            self.assertIn("Created", out.getvalue())

    @patch("main.FROZEN", True)
    def test_init_incomplete_install_errors(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            src_dir = os.path.join(root, "shipped")
            cfg_dir = os.path.join(root, "config")
            os.makedirs(src_dir)  # shipped dir exists but is empty
            err = io.StringIO()
            with (
                patch("main.bundled_config_dir", return_value=src_dir),
                patch("main.config_dir", return_value=cfg_dir),
                contextlib.redirect_stderr(err),
                self.assertRaises(SystemExit) as ctx,
            ):
                main._handle_init()
            self.assertEqual(ctx.exception.code, 2)
            self.assertIn("Shipped config files not found", err.getvalue())

    @patch("main.FROZEN", False)
    def test_init_same_directory_no_samefile_error(self) -> None:
        # dev layout: config_dir IS the bundled dir - must not copy onto itself
        with tempfile.TemporaryDirectory() as cfg_dir:
            self._write_source(cfg_dir)
            with (
                patch("main.bundled_config_dir", return_value=cfg_dir),
                patch("main.config_dir", return_value=cfg_dir),
                patch("main.data_dir", return_value=os.path.join(cfg_dir, "data")),
                patch("main.logs_dir", return_value=os.path.join(cfg_dir, "logs")),
                patch("main.backups_dir", return_value=os.path.join(cfg_dir, "backups")),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                main._handle_init()
            self.assertTrue(os.path.exists(os.path.join(cfg_dir, "config.json")))


if __name__ == "__main__":
    unittest.main()
