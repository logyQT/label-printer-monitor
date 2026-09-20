"""Tests for main.py - entry point and executor.

Tests cover:
- Config loading
- Logging setup
- Collection logic
"""

import os
import sys
import tempfile
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # src/
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)  # project root

import main


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


class TestCollectPrinter(unittest.TestCase):
    """Tests for collect_printer()."""

    def test_successful_collection(self) -> None:
        adapter = MagicMock()
        adapter.get_counters.return_value = {
            "labels_total": 100,
            "meters_total": 50.0,
            "meter_unit": "cm",
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
            "meter_unit": "unknown",
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
            "log_dir": tempfile.mkdtemp(),
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
            "meter_unit": "cm",
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
            "meter_unit": "unknown",
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
            "meter_unit": "cm",
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
            "meter_unit": "cm",
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


if __name__ == "__main__":
    unittest.main()
