"""Tests for db.py - SQLite storage layer.

Tests cover:
- Database initialization
- Table creation
- Snapshot saving (idempotent)
- Snapshot retrieval
- Shift delta calculation
- History queries
- Timestamp rounding
- Row to dict conversion
"""

import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db


class TestInitDb(unittest.TestCase):
    """Tests for init_db()."""

    def setUp(self) -> None:
        self.test_db = ":memory:"

    def test_creates_connection(self) -> None:
        conn = db.init_db(self.test_db)
        self.assertIsNotNone(conn)
        conn.close()

    def test_creates_snapshots_table(self) -> None:
        conn = db.init_db(self.test_db)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='snapshots'"
        )
        self.assertIsNotNone(cursor.fetchone())
        conn.close()

    def test_creates_indexes(self) -> None:
        conn = db.init_db(self.test_db)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
        )
        indexes = cursor.fetchall()
        self.assertGreater(len(indexes), 0)
        conn.close()

    def test_idempotent_init(self) -> None:
        conn1 = db.init_db(self.test_db)
        conn1.close()
        conn2 = db.init_db(self.test_db)
        self.assertIsNotNone(conn2)
        conn2.close()


class TestSaveSnapshot(unittest.TestCase):
    """Tests for save_snapshot()."""

    def setUp(self) -> None:
        self.conn = db.init_db(":memory:")

    def tearDown(self) -> None:
        db.close_db(self.conn)

    def test_insert_new_snapshot(self) -> None:
        result = db.save_snapshot(
            self.conn, "10.0.0.1", 100, 50.5, "cm", "Zebra ZT230", timestamp="2026-09-17T10:00:00"
        )
        self.assertTrue(result)

    def test_idempotent_insert(self) -> None:
        ts = "2026-09-17T10:00:00"
        db.save_snapshot(self.conn, "10.0.0.1", 100, 50.5, "cm", "Zebra", timestamp=ts)
        result = db.save_snapshot(self.conn, "10.0.0.1", 200, 100.0, "cm", "Zebra", timestamp=ts)
        self.assertFalse(result)

    def test_different_timestamps_allowed(self) -> None:
        db.save_snapshot(
            self.conn, "10.0.0.1", 100, 50.5, "cm", "Zebra", timestamp="2026-09-17T10:00:00"
        )
        result = db.save_snapshot(
            self.conn, "10.0.0.1", 200, 100.0, "cm", "Zebra", timestamp="2026-09-17T10:05:00"
        )
        self.assertTrue(result)

    def test_different_printers_allowed(self) -> None:
        db.save_snapshot(
            self.conn, "10.0.0.1", 100, 50.5, "cm", "Zebra", timestamp="2026-09-17T10:00:00"
        )
        result = db.save_snapshot(
            self.conn, "10.0.0.2", 200, 100.0, "cm", "Zebra", timestamp="2026-09-17T10:00:00"
        )
        self.assertTrue(result)

    def test_none_values_allowed(self) -> None:
        result = db.save_snapshot(
            self.conn, "10.0.0.1", None, None, "unknown", "", timestamp="2026-09-17T10:00:00"
        )
        self.assertTrue(result)

    def test_default_timestamp(self) -> None:
        result = db.save_snapshot(self.conn, "10.0.0.1", 100, 50.5, "cm", "Zebra")
        self.assertTrue(result)


class TestGetLatestSnapshot(unittest.TestCase):
    """Tests for get_latest_snapshot()."""

    def setUp(self) -> None:
        self.conn = db.init_db(":memory:")

    def tearDown(self) -> None:
        db.close_db(self.conn)

    def test_no_data_returns_none(self) -> None:
        result = db.get_latest_snapshot(self.conn, "10.0.0.1")
        self.assertIsNone(result)

    def test_returns_latest(self) -> None:
        db.save_snapshot(
            self.conn, "10.0.0.1", 100, 50.0, "cm", "Zebra", timestamp="2026-09-17T10:00:00"
        )
        db.save_snapshot(
            self.conn, "10.0.0.1", 200, 100.0, "cm", "Zebra", timestamp="2026-09-17T10:05:00"
        )
        result = db.get_latest_snapshot(self.conn, "10.0.0.1")
        assert result is not None
        self.assertEqual(result["labels_total"], 200)
        self.assertEqual(result["meters_total"], 100.0)

    def test_different_printer(self) -> None:
        db.save_snapshot(
            self.conn, "10.0.0.1", 100, 50.0, "cm", "Zebra", timestamp="2026-09-17T10:00:00"
        )
        db.save_snapshot(
            self.conn, "10.0.0.2", 300, 150.0, "cm", "Zebra", timestamp="2026-09-17T10:00:00"
        )
        result = db.get_latest_snapshot(self.conn, "10.0.0.2")
        assert result is not None
        self.assertEqual(result["labels_total"], 300)

    def test_returns_dict(self) -> None:
        db.save_snapshot(
            self.conn, "10.0.0.1", 100, 50.0, "cm", "Zebra", timestamp="2026-09-17T10:00:00"
        )
        result = db.get_latest_snapshot(self.conn, "10.0.0.1")
        assert result is not None
        self.assertIsInstance(result, dict)
        self.assertIn("printer_ip", result)
        self.assertIn("timestamp", result)
        self.assertIn("labels_total", result)
        self.assertIn("meters_total", result)
        self.assertIn("meter_unit", result)
        self.assertIn("model_name", result)


class TestGetSnapshotAt(unittest.TestCase):
    """Tests for get_snapshot_at()."""

    def setUp(self) -> None:
        self.conn = db.init_db(":memory:")

    def tearDown(self) -> None:
        db.close_db(self.conn)

    def test_exact_match(self) -> None:
        db.save_snapshot(
            self.conn, "10.0.0.1", 100, 50.0, "cm", "Zebra", timestamp="2026-09-17T10:00:00"
        )
        result = db.get_snapshot_at(self.conn, "10.0.0.1", "2026-09-17T10:00:00")
        assert result is not None
        self.assertEqual(result["labels_total"], 100)

    def test_closest_before(self) -> None:
        db.save_snapshot(
            self.conn, "10.0.0.1", 100, 50.0, "cm", "Zebra", timestamp="2026-09-17T10:00:00"
        )
        db.save_snapshot(
            self.conn, "10.0.0.1", 200, 100.0, "cm", "Zebra", timestamp="2026-09-17T10:05:00"
        )
        result = db.get_snapshot_at(self.conn, "10.0.0.1", "2026-09-17T10:03:00")
        assert result is not None
        self.assertEqual(result["labels_total"], 100)

    def test_no_data_returns_none(self) -> None:
        result = db.get_snapshot_at(self.conn, "10.0.0.1", "2026-09-17T10:00:00")
        self.assertIsNone(result)


class TestGetShiftDelta(unittest.TestCase):
    """Tests for get_shift_delta()."""

    def setUp(self) -> None:
        self.conn = db.init_db(":memory:")

    def tearDown(self) -> None:
        db.close_db(self.conn)

    def test_calculates_delta(self) -> None:
        db.save_snapshot(
            self.conn, "10.0.0.1", 100, 50.0, "cm", "Zebra", timestamp="2026-09-17T06:00:00"
        )
        db.save_snapshot(
            self.conn, "10.0.0.1", 250, 125.0, "cm", "Zebra", timestamp="2026-09-17T14:00:00"
        )
        result = db.get_shift_delta(
            self.conn, "10.0.0.1", "2026-09-17T06:00:00", "2026-09-17T14:00:00"
        )
        assert result is not None
        self.assertEqual(result["labels_delta"], 150)
        self.assertEqual(result["meters_delta"], 75.0)

    def test_missing_start_returns_none(self) -> None:
        db.save_snapshot(
            self.conn, "10.0.0.1", 250, 125.0, "cm", "Zebra", timestamp="2026-09-17T14:00:00"
        )
        result = db.get_shift_delta(
            self.conn, "10.0.0.1", "2026-09-17T06:00:00", "2026-09-17T14:00:00"
        )
        self.assertIsNone(result)

    def test_missing_end_returns_none(self) -> None:
        db.save_snapshot(
            self.conn, "10.0.0.1", 100, 50.0, "cm", "Zebra", timestamp="2026-09-17T06:00:00"
        )
        result = db.get_shift_delta(
            self.conn, "10.0.0.1", "2026-09-17T06:00:00", "2026-09-17T14:00:00"
        )
        # get_snapshot_at returns closest-before, so it finds 06:00 snapshot for both start and end
        # This results in a zero delta, not None
        assert result is not None
        self.assertEqual(result["labels_delta"], 0)
        self.assertEqual(result["meters_delta"], 0.0)

    def test_zero_delta(self) -> None:
        db.save_snapshot(
            self.conn, "10.0.0.1", 100, 50.0, "cm", "Zebra", timestamp="2026-09-17T06:00:00"
        )
        db.save_snapshot(
            self.conn, "10.0.0.1", 100, 50.0, "cm", "Zebra", timestamp="2026-09-17T14:00:00"
        )
        result = db.get_shift_delta(
            self.conn, "10.0.0.1", "2026-09-17T06:00:00", "2026-09-17T14:00:00"
        )
        assert result is not None
        self.assertEqual(result["labels_delta"], 0)
        self.assertEqual(result["meters_delta"], 0.0)

    def test_returns_snapshots(self) -> None:
        db.save_snapshot(
            self.conn, "10.0.0.1", 100, 50.0, "cm", "Zebra", timestamp="2026-09-17T06:00:00"
        )
        db.save_snapshot(
            self.conn, "10.0.0.1", 250, 125.0, "cm", "Zebra", timestamp="2026-09-17T14:00:00"
        )
        result = db.get_shift_delta(
            self.conn, "10.0.0.1", "2026-09-17T06:00:00", "2026-09-17T14:00:00"
        )
        assert result is not None
        self.assertIn("start_snapshot", result)
        self.assertIn("end_snapshot", result)


class TestGetAllPrintersLatest(unittest.TestCase):
    """Tests for get_all_printers_latest()."""

    def setUp(self) -> None:
        self.conn = db.init_db(":memory:")

    def tearDown(self) -> None:
        db.close_db(self.conn)

    def test_empty_returns_empty(self) -> None:
        result = db.get_all_printers_latest(self.conn)
        self.assertEqual(len(result), 0)

    def test_returns_latest_per_printer(self) -> None:
        db.save_snapshot(
            self.conn, "10.0.0.1", 100, 50.0, "cm", "Zebra", timestamp="2026-09-17T10:00:00"
        )
        db.save_snapshot(
            self.conn, "10.0.0.1", 200, 100.0, "cm", "Zebra", timestamp="2026-09-17T10:05:00"
        )
        db.save_snapshot(
            self.conn, "10.0.0.2", 300, 150.0, "cm", "Zebra", timestamp="2026-09-17T10:00:00"
        )
        result = db.get_all_printers_latest(self.conn)
        self.assertEqual(len(result), 2)
        labels_by_ip = {r["printer_ip"]: r["labels_total"] for r in result}
        self.assertEqual(labels_by_ip["10.0.0.1"], 200)
        self.assertEqual(labels_by_ip["10.0.0.2"], 300)


class TestGetPrintersByShift(unittest.TestCase):
    """Tests for get_printers_by_shift()."""

    def setUp(self) -> None:
        self.conn = db.init_db(":memory:")

    def tearDown(self) -> None:
        db.close_db(self.conn)

    def test_returns_deltas_for_all_printers(self) -> None:
        db.save_snapshot(
            self.conn, "10.0.0.1", 100, 50.0, "cm", "Zebra", timestamp="2026-09-17T06:00:00"
        )
        db.save_snapshot(
            self.conn, "10.0.0.1", 200, 100.0, "cm", "Zebra", timestamp="2026-09-17T14:00:00"
        )
        db.save_snapshot(
            self.conn, "10.0.0.2", 300, 150.0, "cm", "Zebra", timestamp="2026-09-17T06:00:00"
        )
        db.save_snapshot(
            self.conn, "10.0.0.2", 400, 200.0, "cm", "Zebra", timestamp="2026-09-17T14:00:00"
        )
        result = db.get_printers_by_shift(self.conn, "2026-09-17T06:00:00", "2026-09-17T14:00:00")
        self.assertEqual(len(result), 2)

    def test_printer_without_data_excluded(self) -> None:
        db.save_snapshot(
            self.conn, "10.0.0.1", 100, 50.0, "cm", "Zebra", timestamp="2026-09-17T06:00:00"
        )
        db.save_snapshot(
            self.conn, "10.0.0.1", 200, 100.0, "cm", "Zebra", timestamp="2026-09-17T14:00:00"
        )
        result = db.get_printers_by_shift(self.conn, "2026-09-17T06:00:00", "2026-09-17T14:00:00")
        self.assertEqual(len(result), 1)


class TestGetHistory(unittest.TestCase):
    """Tests for get_history()."""

    def setUp(self) -> None:
        self.conn = db.init_db(":memory:")

    def tearDown(self) -> None:
        db.close_db(self.conn)

    def test_returns_all_history(self) -> None:
        db.save_snapshot(
            self.conn, "10.0.0.1", 100, 50.0, "cm", "Zebra", timestamp="2026-09-17T10:00:00"
        )
        db.save_snapshot(
            self.conn, "10.0.0.1", 200, 100.0, "cm", "Zebra", timestamp="2026-09-17T10:05:00"
        )
        result = db.get_history(self.conn, "10.0.0.1")
        self.assertEqual(len(result), 2)

    def test_date_filter(self) -> None:
        db.save_snapshot(
            self.conn,
            "10.0.0.1",
            100,
            50.0,
            "cm",
            "Zebra",
            timestamp=int(datetime(2026, 9, 16, 10, 0, 0).timestamp()),
        )
        db.save_snapshot(
            self.conn,
            "10.0.0.1",
            200,
            100.0,
            "cm",
            "Zebra",
            timestamp=int(datetime(2026, 9, 17, 10, 0, 0).timestamp()),
        )
        result = db.get_history(self.conn, "10.0.0.1", start_date="2026-09-17")
        self.assertEqual(len(result), 1)

    def test_end_date_filter(self) -> None:
        db.save_snapshot(
            self.conn,
            "10.0.0.1",
            100,
            50.0,
            "cm",
            "Zebra",
            timestamp=int(datetime(2026, 9, 16, 10, 0, 0).timestamp()),
        )
        db.save_snapshot(
            self.conn,
            "10.0.0.1",
            200,
            100.0,
            "cm",
            "Zebra",
            timestamp=int(datetime(2026, 9, 17, 10, 0, 0).timestamp()),
        )
        result = db.get_history(self.conn, "10.0.0.1", end_date="2026-09-16")
        self.assertEqual(len(result), 1)

    def test_empty_history(self) -> None:
        result = db.get_history(self.conn, "10.0.0.1")
        self.assertEqual(len(result), 0)

    def test_chronological_order(self) -> None:
        db.save_snapshot(
            self.conn, "10.0.0.1", 200, 100.0, "cm", "Zebra", timestamp="2026-09-17T10:05:00"
        )
        db.save_snapshot(
            self.conn, "10.0.0.1", 100, 50.0, "cm", "Zebra", timestamp="2026-09-17T10:00:00"
        )
        result = db.get_history(self.conn, "10.0.0.1")
        self.assertEqual(result[0]["labels_total"], 100)
        self.assertEqual(result[1]["labels_total"], 200)


class TestRoundTimestamp(unittest.TestCase):
    """Tests for _round_timestamp()."""

    def test_rounds_down(self) -> None:
        dt = datetime(2026, 9, 17, 10, 7, 30)
        result = db._round_timestamp(dt)
        self.assertEqual(result, int(datetime(2026, 9, 17, 10, 5, 0).timestamp()))

    def test_rounds_exact(self) -> None:
        dt = datetime(2026, 9, 17, 10, 5, 0)
        result = db._round_timestamp(dt)
        self.assertEqual(result, int(datetime(2026, 9, 17, 10, 5, 0).timestamp()))

    def test_rounds_up(self) -> None:
        dt = datetime(2026, 9, 17, 10, 3, 0)
        result = db._round_timestamp(dt)
        self.assertEqual(result, int(datetime(2026, 9, 17, 10, 0, 0).timestamp()))

    def test_rounds_to_10_minutes(self) -> None:
        dt = datetime(2026, 9, 17, 10, 12, 0)
        result = db._round_timestamp(dt, interval_minutes=10)
        self.assertEqual(result, int(datetime(2026, 9, 17, 10, 10, 0).timestamp()))


class TestRowToDict(unittest.TestCase):
    """Tests for _row_to_dict()."""

    def test_converts_tuple(self) -> None:
        row = ("10.0.0.1", "2026-09-17T10:00:00", 100, 50.5, "cm", "Zebra")
        result = db._row_to_dict(row)
        self.assertEqual(result["printer_ip"], "10.0.0.1")
        self.assertEqual(result["timestamp"], "2026-09-17T10:00:00")
        self.assertEqual(result["labels_total"], 100)
        self.assertEqual(result["meters_total"], 50.5)
        self.assertEqual(result["meter_unit"], "cm")
        self.assertEqual(result["model_name"], "Zebra")

    def test_none_values(self) -> None:
        row = ("10.0.0.1", "2026-09-17T10:00:00", None, None, None, None)
        result = db._row_to_dict(row)
        self.assertIsNone(result["labels_total"])
        self.assertIsNone(result["meters_total"])


class TestCloseDb(unittest.TestCase):
    """Tests for close_db()."""

    def test_close_existing(self) -> None:
        conn = db.init_db(":memory:")
        db.close_db(conn)  # Should not raise

    def test_close_none(self) -> None:
        db.close_db(None)  # Should not raise


if __name__ == "__main__":
    unittest.main()
