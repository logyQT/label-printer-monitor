"""Tests for main.py - entry point and executor.

Tests cover:
- Subcommand dispatch (lpm <command> [flags])
- Config loading
- Logging setup
- Collection logic
"""

import contextlib
import io
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import main as main


class TestLoadConfig(unittest.TestCase):
    """Tests for load_config()."""

    def test_load_valid_config(self) -> None:
        config = main.load_config()
        self.assertIn("printers", config)
        self.assertIn("snmp", config)
        self.assertIn("db", config)

    def test_load_config_with_printers(self) -> None:
        config = main.load_config()
        self.assertGreater(len(config["printers"]), 0)
        printer = config["printers"][0]
        self.assertIn("ip", printer)
        self.assertIn("model", printer)
        self.assertIn("location", printer)


class TestSetupLogging(unittest.TestCase):
    """Tests for setup_logging()."""

    def _close_logging_handlers(self) -> None:
        """Close logging handlers so files are not locked on Windows."""
        import logging

        logger = logging.getLogger("printer_stats")
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)

    def test_creates_log_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = os.path.join(tmpdir, "test_logs")
            log_file = main.setup_logging(log_dir)
            self.assertTrue(os.path.exists(log_dir))
            self.assertTrue(os.path.exists(log_file))
            self._close_logging_handlers()

    def test_log_file_name_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = main.setup_logging(tmpdir)
            basename = os.path.basename(log_file)
            self.assertTrue(basename.startswith("run_"))
            self.assertTrue(basename.endswith(".log"))
            self._close_logging_handlers()


class TestPruneLogs(unittest.TestCase):
    """Tests for _prune_logs()."""

    def _make_log(self, directory: str, ts: str) -> str:
        path = os.path.join(directory, f"run_{ts}.log")
        with open(path, "w", encoding="utf-8") as f:
            f.write("log line\n")
        return path

    def test_removes_old_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            old = self._make_log(tmpdir, (datetime.now() - timedelta(days=40)).strftime("%Y%m%d_%H%M%S"))
            recent = self._make_log(tmpdir, (datetime.now() - timedelta(days=5)).strftime("%Y%m%d_%H%M%S"))
            main._prune_logs(tmpdir, 30)
            self.assertFalse(os.path.exists(old))
            self.assertTrue(os.path.exists(recent))

    def test_zero_disables_pruning(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            old = self._make_log(tmpdir, (datetime.now() - timedelta(days=400)).strftime("%Y%m%d_%H%M%S"))
            main._prune_logs(tmpdir, 0)
            self.assertTrue(os.path.exists(old))

    def test_skips_non_matching_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            foreign = os.path.join(tmpdir, "other.txt")
            with open(foreign, "w", encoding="utf-8") as f:
                f.write("keep me\n")
            malformed = os.path.join(tmpdir, "run_not-a-date.log")
            with open(malformed, "w", encoding="utf-8") as f:
                f.write("keep me too\n")
            ancient = self._make_log(tmpdir, (datetime.now() - timedelta(days=400)).strftime("%Y%m%d_%H%M%S"))
            main._prune_logs(tmpdir, 30)
            self.assertTrue(os.path.exists(foreign))
            self.assertTrue(os.path.exists(malformed))
            self.assertFalse(os.path.exists(ancient))

    def test_missing_directory_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            main._prune_logs(os.path.join(tmpdir, "nope"), 30)  # must not raise


class TestCollectPrinter(unittest.TestCase):
    """Tests for collect_printer()."""

    def test_successful_collection(self) -> None:
        adapter = MagicMock()
        adapter.get_counters.return_value = {
            "labels_total": 100,
            "meters_total": 50.0,
            "model_name": "Zebra ZT230",
            "reachable": True,
        }
        config = {"ip": "10.0.0.1", "location": "Line 1"}
        result = main.collect_printer(adapter, config)
        assert result is not None
        self.assertEqual(result["labels_total"], 100)

    def test_unreachable_printer(self) -> None:
        adapter = MagicMock()
        adapter.get_counters.return_value = {
            "labels_total": None,
            "meters_total": None,
            "model_name": "",
            "reachable": False,
        }
        config = {"ip": "10.0.0.1", "location": "Line 1"}
        result = main.collect_printer(adapter, config)
        self.assertIsNone(result)

    def test_exception_returns_none(self) -> None:
        adapter = MagicMock()
        adapter.get_counters.side_effect = Exception("SNMP error")
        config = {"ip": "10.0.0.1", "location": "Line 1"}
        result = main.collect_printer(adapter, config)
        self.assertIsNone(result)


class TestRunCollection(unittest.TestCase):
    """Tests for run_collection()."""

    def setUp(self) -> None:
        self.config = {
            "db": {"filename": ":memory:"},
            "logs": {"dir": tempfile.mkdtemp()},
            "snmp": {"community": "public", "timeout_sec": 1, "retries": 0},
            "printers": [
                {"ip": "10.0.0.1", "model": "Zebra ZT230", "location": "Line 1"},
            ],
        }

    @patch("main.create_adapter")
    def test_successful_collection(self, mock_create: MagicMock) -> None:
        adapter = MagicMock()
        adapter.get_counters.return_value = {
            "labels_total": 100,
            "meters_total": 50.0,
            "model_name": "Zebra ZT230",
            "reachable": True,
        }
        mock_create.return_value = adapter
        success, fail, total = main.run_collection(self.config)
        self.assertEqual(success, 1)
        self.assertEqual(fail, 0)
        self.assertEqual(total, 1)

    @patch("main.create_adapter")
    def test_failed_collection(self, mock_create: MagicMock) -> None:
        adapter = MagicMock()
        adapter.get_counters.return_value = {
            "labels_total": None,
            "meters_total": None,
            "model_name": "",
            "reachable": False,
        }
        mock_create.return_value = adapter
        success, fail, total = main.run_collection(self.config)
        self.assertEqual(success, 0)
        self.assertEqual(fail, 1)

    @patch("main.create_adapter")
    def test_mixed_success_and_config_error(self, mock_create: MagicMock) -> None:
        """A config error (unknown model) on one printer doesn't affect the rest."""
        adapter = MagicMock()
        adapter.get_counters.return_value = {
            "labels_total": 100,
            "meters_total": 50.0,
            "model_name": "Zebra ZT230",
            "reachable": True,
        }

        def fake_create(model: str, ip: str, **kwargs: Any) -> MagicMock:
            if model == "No Such Model":
                raise ValueError(f"Unknown model: {model}")
            return adapter

        mock_create.side_effect = fake_create
        self.config["printers"] = [
            {"ip": "10.0.0.1", "model": "Zebra ZT230", "location": "Line 1"},
            {"ip": "10.0.0.2", "model": "No Such Model", "location": "Line 2"},
        ]
        success, fail, total = main.run_collection(self.config)
        self.assertEqual((success, fail, total), (1, 1, 2))

    def test_honors_max_concurrency(self) -> None:
        """max_concurrency from the collection config section is applied."""
        from concurrent.futures import ThreadPoolExecutor as RealTPE

        captured = {}

        def fake_tpe(max_workers: int | None = None, **kwargs: Any) -> Any:
            captured["max_workers"] = max_workers
            return RealTPE(max_workers=max_workers)

        adapter = MagicMock()
        adapter.get_counters.return_value = {
            "labels_total": 100,
            "meters_total": 50.0,
            "model_name": "Zebra ZT230",
            "reachable": True,
        }
        self.config["collection"] = {"max_concurrency": 5}
        with (
            patch("main.create_adapter", return_value=adapter),
            patch("main.concurrent.futures.ThreadPoolExecutor", side_effect=fake_tpe),
        ):
            success, fail, total = main.run_collection(self.config)

        self.assertEqual(captured.get("max_workers"), 5)
        self.assertEqual((success, fail, total), (1, 0, 1))


class TestSubcommandDispatch(unittest.TestCase):
    """lpm <command> [flags] - subparser routing and flag ownership."""

    def test_bare_invocation_without_tty_prints_full_help(self) -> None:
        """Piped/redirected stdin gets help (never blocks on input())."""
        out = io.StringIO()
        with (
            patch("sys.argv", ["lpm"]),
            patch("sys.stdin") as stdin,
            patch("main.run_shell") as shell,
            contextlib.redirect_stdout(out),
        ):
            stdin.isatty.return_value = False
            main.main()  # no SystemExit: prints help and returns (exit 0)
        shell.assert_not_called()
        text = out.getvalue()
        self.assertIn("usage:", text)
        self.assertIn("collect", text)
        self.assertIn("schedule", text)
        # full help includes each command's flags, not just the summaries
        self.assertIn("--network", text)
        self.assertIn("--csv", text)

    def test_bare_invocation_on_tty_starts_shell(self) -> None:
        with patch("sys.argv", ["lpm"]), patch("sys.stdin") as stdin, patch("main.run_shell") as shell:
            stdin.isatty.return_value = True
            main.main()
        shell.assert_called_once_with(main.build_parser)

    def test_shell_subcommand_starts_shell(self) -> None:
        with patch("sys.argv", ["lpm", "shell"]), patch("main.run_shell") as shell:
            main.main()
        shell.assert_called_once_with(main.build_parser)

    def test_help_prints_full_help(self) -> None:
        out = io.StringIO()
        with patch("sys.argv", ["lpm", "help"]), contextlib.redirect_stdout(out):
            main.main()  # no SystemExit: help returns (exit 0)
        text = out.getvalue()
        self.assertIn("usage:", text)
        self.assertIn("command details:", text)
        self.assertIn("--network", text)
        self.assertIn("--csv", text)

    def test_help_topic_prints_single_command_help(self) -> None:
        out = io.StringIO()
        with patch("sys.argv", ["lpm", "help", "report"]), contextlib.redirect_stdout(out):
            main.main()
        text = out.getvalue()
        self.assertIn("--from", text)
        self.assertIn("--csv", text)
        self.assertNotIn("--network", text)

    def test_help_unknown_topic_exits_2(self) -> None:
        err = io.StringIO()
        with (
            patch("sys.argv", ["lpm", "help", "frobnicate"]),
            contextlib.redirect_stderr(err),
            self.assertRaises(SystemExit) as ctx,
        ):
            main.main()
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("frobnicate", err.getvalue())

    def test_every_subcommand_routes_to_its_handler(self) -> None:
        cases = [
            ("init", "_handle_init"),
            ("collect", "_handle_collect"),
            ("validate", "_handle_validate"),
            ("report", "_handle_report"),
            ("schedule", "_handle_schedule"),
            ("purge", "_handle_purge"),
            ("help", "_handle_help"),
            ("shell", "_handle_shell"),
        ]
        for command, handler_name in cases:
            with (
                self.subTest(command=command),
                patch("sys.argv", ["lpm", command]),
                patch(f"main.{handler_name}") as handler,
            ):
                main.main()
            handler.assert_called_once()

    def test_collect_flags_parsed(self) -> None:
        with (
            patch("sys.argv", ["lpm", "collect", "-v", "--dry"]),
            patch("main._handle_collect") as handler,
        ):
            main.main()
        namespace = handler.call_args.args[0]
        self.assertTrue(namespace.verbose)
        self.assertTrue(namespace.dry)

    def test_schedule_remove_and_verbose_flags(self) -> None:
        with (
            patch("sys.argv", ["lpm", "schedule", "-r", "-v"]),
            patch("main._handle_schedule") as handler,
        ):
            main.main()
        namespace = handler.call_args.args[0]
        self.assertTrue(namespace.remove)
        self.assertTrue(namespace.verbose)

    def test_purge_flags_parsed(self) -> None:
        with (
            patch("sys.argv", ["lpm", "purge", "--all", "--dry-run"]),
            patch("main._handle_purge") as handler,
        ):
            main.main()
        namespace = handler.call_args.args[0]
        self.assertTrue(namespace.all)
        self.assertTrue(namespace.dry_run)
        self.assertFalse(namespace.tasks)  # --all does not rewrite the flags

    def test_report_rejects_verbose(self) -> None:
        """-v belongs to collect/validate/schedule only - report errors out."""
        err = io.StringIO()
        with (
            patch("sys.argv", ["lpm", "report", "-v"]),
            contextlib.redirect_stderr(err),
            self.assertRaises(SystemExit) as ctx,
        ):
            main.main()
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("unrecognized arguments", err.getvalue())

    def test_flag_before_subcommand_rejected(self) -> None:
        """Flags must follow the subcommand: `lpm -v collect` is an error."""
        with (
            patch("sys.argv", ["lpm", "-v", "collect"]),
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as ctx,
        ):
            main.main()
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
