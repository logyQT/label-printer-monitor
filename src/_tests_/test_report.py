"""Tests for report.py - shift-based printer statistics.

Tests cover:
- Epoch / shift helper functions
- Unit conversion
- compute_weekly (with mocked DB)
- print_report (stdout output)
- export_csv (file output)
"""

import csv
import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any

import db
import report

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config() -> dict[str, Any]:
    """Return a minimal config dict for tests."""
    return {
        "db": {"filename": ":memory:"},
        "printers": [
            {"ip": "10.0.0.1", "model": "Zebra ZT230", "location": "Line 1"},
            {"ip": "10.0.0.2", "model": "Sato CL4NX Plus", "location": "Line 2"},
        ],
        "shifts": [
            {"name": "Morning", "start": "06:00", "end": "14:00"},
            {"name": "Afternoon", "start": "14:00", "end": "22:00"},
        ],
    }


def _seed_snapshots(
    conn: Any, printer_ip: str, timestamps_labels_meters_unit: list[tuple[Any, Any, Any, str]]
) -> None:
    """Insert snapshot rows from a list of (epoch, labels, meters, unit)."""
    for ts, labels, meters, unit in timestamps_labels_meters_unit:
        db.save_snapshot(
            conn, printer_ip, labels, meters, unit, model_name="TestModel", timestamp=ts
        )


def _epoch_for_date(date_str: str, hour: int, minute: int = 0) -> int:
    """Return epoch int for a date+time (UTC)."""
    dt = datetime.fromisoformat(f"{date_str}T{hour:02d}:{minute:02d}:00+00:00")
    return int(dt.timestamp())


# ---------------------------------------------------------------------------
# Tests for _to_epoch
# ---------------------------------------------------------------------------


class TestToEpoch(unittest.TestCase):
    """Tests for _to_epoch()."""

    def test_known_date(self) -> None:
        result = report._to_epoch("2026-09-17")
        # Should be a reasonable epoch for 2026-09-17 00:00:00 UTC
        self.assertIsInstance(result, int)
        self.assertGreater(result, 0)

    def test_returns_int(self) -> None:
        result = report._to_epoch("2020-01-01")
        self.assertIsInstance(result, int)


# ---------------------------------------------------------------------------
# Tests for _convert_to_meters
# ---------------------------------------------------------------------------


class TestConvertToMeters(unittest.TestCase):
    """Tests for _convert_to_meters()."""

    def test_cm_to_m(self) -> None:
        result = report._convert_to_meters(100, "cm")
        assert result is not None
        self.assertAlmostEqual(result, 1.0)

    def test_m_passthrough(self) -> None:
        result = report._convert_to_meters(5.5, "m")
        assert result is not None
        self.assertAlmostEqual(result, 5.5)

    def test_in_to_m(self) -> None:
        result = report._convert_to_meters(1, "in")
        assert result is not None
        self.assertAlmostEqual(result, 0.0254)

    def test_mm_to_m(self) -> None:
        result = report._convert_to_meters(1000, "mm")
        assert result is not None
        self.assertAlmostEqual(result, 1.0)

    def test_none_returns_none(self) -> None:
        self.assertIsNone(report._convert_to_meters(None, "cm"))

    def test_unknown_unit_passthrough(self) -> None:
        result = report._convert_to_meters(42, "ft")
        assert result is not None
        self.assertAlmostEqual(result, 42)


# ---------------------------------------------------------------------------
# Tests for compute_weekly
# ---------------------------------------------------------------------------


class TestComputeWeekly(unittest.TestCase):
    """Tests for compute_weekly() with real in-memory DB."""

    def test_basic_morning_shift(self) -> None:
        """Two snapshots in the same week should produce a delta."""
        config = _make_config()
        conn = db.init_db(":memory:")

        # 2026-09-17: Morning shift = 06:00–14:00 UTC
        ts_start = _epoch_for_date("2026-09-17", 7, 0)
        ts_end = _epoch_for_date("2026-09-17", 12, 0)
        db.save_snapshot(conn, "10.0.0.1", 100, 50.0, "cm", "Zebra ZT230", timestamp=ts_start)
        db.save_snapshot(conn, "10.0.0.1", 120, 60.0, "cm", "Zebra ZT230", timestamp=ts_end)

        # Patch to return our still-open connection
        with patch("report.db.init_db", return_value=conn), patch("report.db.close_db"):
            weeks = report.compute_weekly(config, "2026-09-17", "2026-09-17")

        db.close_db(conn)
        self.assertIsInstance(weeks, dict)
        self.assertTrue(len(weeks) >= 1)
        all_printers = [r for printers in weeks.values() for r in printers]
        zebras = [r for r in all_printers if r["ip"] == "10.0.0.1"]
        self.assertTrue(len(zebras) >= 1)
        r = zebras[0]
        self.assertEqual(r["labels"], 20)
        assert r["meters"] is not None
        self.assertAlmostEqual(r["meters"], 0.1)  # 10 cm = 0.1 m

    def test_no_snapshots_returns_empty(self) -> None:
        """No snapshots in DB → empty results."""
        config = _make_config()
        conn = db.init_db(":memory:")

        with patch("report.db.init_db", return_value=conn), patch("report.db.close_db"):
            weeks = report.compute_weekly(config, "2026-09-17", "2026-09-17")

        db.close_db(conn)
        self.assertEqual(weeks, {})

    def test_multiple_printers(self) -> None:
        """Snapshots for two printers should yield rows for each."""
        config = _make_config()
        conn = db.init_db(":memory:")

        for ip in ("10.0.0.1", "10.0.0.2"):
            db.save_snapshot(
                conn, ip, 10, 1.0, "m", "Model", timestamp=_epoch_for_date("2026-09-17", 8, 0)
            )
            db.save_snapshot(
                conn, ip, 20, 2.0, "m", "Model", timestamp=_epoch_for_date("2026-09-17", 10, 0)
            )

        with patch("report.db.init_db", return_value=conn), patch("report.db.close_db"):
            weeks = report.compute_weekly(config, "2026-09-17", "2026-09-17")

        db.close_db(conn)
        all_ips = {r["ip"] for printers in weeks.values() for r in printers}
        self.assertIn("10.0.0.1", all_ips)
        self.assertIn("10.0.0.2", all_ips)

    def test_multiple_weeks(self) -> None:
        """Snapshots on two consecutive weeks produce entries for each week."""
        config = _make_config()
        conn = db.init_db(":memory:")

        for day in ("2026-09-07", "2026-09-14"):
            db.save_snapshot(
                conn, "10.0.0.1", 50, 5.0, "m", "Zebra ZT230", timestamp=_epoch_for_date(day, 8, 0)
            )
            db.save_snapshot(
                conn, "10.0.0.1", 60, 6.0, "m", "Zebra ZT230", timestamp=_epoch_for_date(day, 10, 0)
            )

        with patch("report.db.init_db", return_value=conn), patch("report.db.close_db"):
            weeks = report.compute_weekly(config, "2026-09-07", "2026-09-14")

        db.close_db(conn)
        self.assertIsInstance(weeks, dict)
        self.assertTrue(len(weeks) >= 2)

    def test_single_snapshot_skipped(self) -> None:
        """A single snapshot in a week produces a zero delta."""
        config = _make_config()
        conn = db.init_db(":memory:")

        db.save_snapshot(
            conn,
            "10.0.0.1",
            100,
            50.0,
            "cm",
            "Zebra ZT230",
            timestamp=_epoch_for_date("2026-09-17", 9, 0),
        )

        with patch("report.db.init_db", return_value=conn), patch("report.db.close_db"):
            weeks = report.compute_weekly(config, "2026-09-17", "2026-09-17")

        db.close_db(conn)
        self.assertIsInstance(weeks, dict)
        all_printers = [r for printers in weeks.values() for r in printers]
        self.assertEqual(len(all_printers), 1)
        self.assertEqual(all_printers[0]["labels"], 0)
        assert all_printers[0]["meters"] is not None
        self.assertAlmostEqual(all_printers[0]["meters"], 0.0)


# ---------------------------------------------------------------------------
# Tests for print_report
# ---------------------------------------------------------------------------


class TestPrintReport(unittest.TestCase):
    """Tests for print_report()."""

    def _get_printed_text(self, mock_print: Any) -> str:
        """Collect all printed text from a mock print."""
        lines = []
        for c in mock_print.call_args_list:
            if c.args:
                lines.append(str(c.args[0]))
        return "\n".join(lines)

    def test_prints_zebra_table(self) -> None:
        config = _make_config()
        weeks: report.Weeks = {
            "2026-W37": [
                {"ip": "10.0.0.1", "model": "Zebra ZT230", "labels": 150, "meters": 0.5},
            ],
        }
        with patch("builtins.print") as mock_print:
            report.print_report(weeks, config)
        text = self._get_printed_text(mock_print)
        self.assertIn("Zebra", text)
        self.assertIn("10.0.0.1", text)
        self.assertIn("150", text)

    def test_prints_sato_table(self) -> None:
        config = _make_config()
        weeks: report.Weeks = {
            "2026-W37": [
                {"ip": "10.0.0.2", "model": "Sato CL4NX Plus", "labels": None, "meters": 3.2},
            ],
        }
        with patch("builtins.print") as mock_print:
            report.print_report(weeks, config)
        text = self._get_printed_text(mock_print)
        self.assertIn("Sato", text)
        self.assertIn("3.2", text)

    def test_empty_results(self) -> None:
        config = _make_config()
        with patch("builtins.print") as mock_print:
            report.print_report({}, config)
        text = self._get_printed_text(mock_print)
        self.assertNotIn("WEEK ", text)
        self.assertIn("GRAND TOTAL", text)


# ---------------------------------------------------------------------------
# Tests for export_csv
# ---------------------------------------------------------------------------


class TestExportCsv(unittest.TestCase):
    """Tests for export_csv()."""

    def test_creates_csv_with_headers(self) -> None:
        config = _make_config()
        weeks: report.Weeks = {
            "2026-W37": [
                {"ip": "10.0.0.1", "model": "Zebra ZT230", "labels": 100, "meters": 1.5},
                {"ip": "10.0.0.2", "model": "Sato CL4NX Plus", "labels": None, "meters": 3.0},
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "report.csv")
            report.export_csv(weeks, config, csv_path)
            self.assertTrue(os.path.exists(csv_path))
            with open(csv_path, encoding="utf-8") as f:
                reader = csv.reader(f)
                headers = next(reader)
                self.assertEqual(
                    headers, ["Week", "IP", "Model", "Labels Delta", "Meters Delta (m)"]
                )

    def test_csv_row_count(self) -> None:
        config = _make_config()
        weeks: report.Weeks = {
            "2026-W37": [
                {"ip": "10.0.0.1", "model": "Zebra ZT230", "labels": 50, "meters": 0.5},
            ],
            "2026-W38": [
                {"ip": "10.0.0.1", "model": "Zebra ZT230", "labels": 70, "meters": 0.8},
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "report.csv")
            report.export_csv(weeks, config, csv_path)
            with open(csv_path, encoding="utf-8") as f:
                lines = f.read().strip().split("\n")
                # header + 2 data rows
                self.assertEqual(len(lines), 3)

    def test_none_values_written_as_empty(self) -> None:
        config = _make_config()
        weeks: report.Weeks = {
            "2026-W37": [
                {"ip": "10.0.0.1", "model": "Zebra ZT230", "labels": None, "meters": None},
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "report.csv")
            report.export_csv(weeks, config, csv_path)
            with open(csv_path, encoding="utf-8") as f:
                reader = csv.reader(f)
                next(reader)  # skip header
                row = next(reader)
                # labels and meters columns should be empty strings
                self.assertEqual(row[3], "")
                self.assertEqual(row[4], "")


# ---------------------------------------------------------------------------
# Tests for main() argument parsing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
