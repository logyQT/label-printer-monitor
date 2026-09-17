"""Tests for db.py - SQLite storage layer.

Tests cover:
- Database initialization
- Table creation
- Snapshot saving (idempotent)
- Snapshot retrieval
- Shift delta calculation
- History queries
- Unit caching
- Timestamp rounding
- Row to dict conversion
"""

import sqlite3
import sys
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db


class TestInitDb(unittest.TestCase):
    """Tests for init_db()."""

    def setUp(self):
        self.test_db = ':memory:'

    def test_creates_connection(self):
        conn = db.init_db(self.test_db)
        self.assertIsNotNone(conn)
        conn.close()

    def test_creates_snapshots_table(self):
        conn = db.init_db(self.test_db)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='snapshots'"
        )
        self.assertIsNotNone(cursor.fetchone())
        conn.close()

    def test_creates_printer_units_table(self):
        conn = db.init_db(self.test_db)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='printer_units'"
        )
        self.assertIsNotNone(cursor.fetchone())
        conn.close()

    def test_creates_indexes(self):
        conn = db.init_db(self.test_db)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
        )
        indexes = cursor.fetchall()
        self.assertGreater(len(indexes), 0)
        conn.close()

    def test_idempotent_init(self):
        conn1 = db.init_db(self.test_db)
        conn1.close()
        conn2 = db.init_db(self.test_db)
        self.assertIsNotNone(conn2)
        conn2.close()


class TestSaveSnapshot(unittest.TestCase):
    """Tests for save_snapshot()."""

    def setUp(self):
        self.conn = db.init_db(':memory:')

    def tearDown(self):
        db.close_db(self.conn)

    def test_insert_new_snapshot(self):
        result = db.save_snapshot(
            self.conn, '10.0.0.1', 100, 50.5, 'cm',
            'Zebra ZT230', 'ABC123', 'idle',
            timestamp='2026-09-17T10:00:00'
        )
        self.assertTrue(result)

    def test_idempotent_insert(self):
        ts = '2026-09-17T10:00:00'
        db.save_snapshot(self.conn, '10.0.0.1', 100, 50.5, 'cm', 'Zebra', 'SN', 'idle', timestamp=ts)
        result = db.save_snapshot(self.conn, '10.0.0.1', 200, 100.0, 'cm', 'Zebra', 'SN', 'idle', timestamp=ts)
        self.assertFalse(result)

    def test_different_timestamps_allowed(self):
        db.save_snapshot(self.conn, '10.0.0.1', 100, 50.5, 'cm', 'Zebra', 'SN', 'idle', timestamp='2026-09-17T10:00:00')
        result = db.save_snapshot(self.conn, '10.0.0.1', 200, 100.0, 'cm', 'Zebra', 'SN', 'idle', timestamp='2026-09-17T10:05:00')
        self.assertTrue(result)

    def test_different_printers_allowed(self):
        db.save_snapshot(self.conn, '10.0.0.1', 100, 50.5, 'cm', 'Zebra', 'SN1', 'idle', timestamp='2026-09-17T10:00:00')
        result = db.save_snapshot(self.conn, '10.0.0.2', 200, 100.0, 'cm', 'Zebra', 'SN2', 'idle', timestamp='2026-09-17T10:00:00')
        self.assertTrue(result)

    def test_none_values_allowed(self):
        result = db.save_snapshot(
            self.conn, '10.0.0.1', None, None, 'unknown',
            '', '', 'offline',
            timestamp='2026-09-17T10:00:00'
        )
        self.assertTrue(result)

    def test_default_timestamp(self):
        result = db.save_snapshot(self.conn, '10.0.0.1', 100, 50.5, 'cm', 'Zebra', 'SN', 'idle')
        self.assertTrue(result)


class TestGetLatestSnapshot(unittest.TestCase):
    """Tests for get_latest_snapshot()."""

    def setUp(self):
        self.conn = db.init_db(':memory:')

    def tearDown(self):
        db.close_db(self.conn)

    def test_no_data_returns_none(self):
        result = db.get_latest_snapshot(self.conn, '10.0.0.1')
        self.assertIsNone(result)

    def test_returns_latest(self):
        db.save_snapshot(self.conn, '10.0.0.1', 100, 50.0, 'cm', 'Zebra', 'SN', 'idle', timestamp='2026-09-17T10:00:00')
        db.save_snapshot(self.conn, '10.0.0.1', 200, 100.0, 'cm', 'Zebra', 'SN', 'idle', timestamp='2026-09-17T10:05:00')
        result = db.get_latest_snapshot(self.conn, '10.0.0.1')
        self.assertEqual(result['labels_total'], 200)
        self.assertEqual(result['meters_total'], 100.0)

    def test_different_printer(self):
        db.save_snapshot(self.conn, '10.0.0.1', 100, 50.0, 'cm', 'Zebra', 'SN', 'idle', timestamp='2026-09-17T10:00:00')
        db.save_snapshot(self.conn, '10.0.0.2', 300, 150.0, 'cm', 'Zebra', 'SN2', 'idle', timestamp='2026-09-17T10:00:00')
        result = db.get_latest_snapshot(self.conn, '10.0.0.2')
        self.assertEqual(result['labels_total'], 300)

    def test_returns_dict(self):
        db.save_snapshot(self.conn, '10.0.0.1', 100, 50.0, 'cm', 'Zebra', 'SN', 'idle', timestamp='2026-09-17T10:00:00')
        result = db.get_latest_snapshot(self.conn, '10.0.0.1')
        self.assertIsInstance(result, dict)
        self.assertIn('printer_ip', result)
        self.assertIn('timestamp', result)
        self.assertIn('labels_total', result)
        self.assertIn('meters_total', result)
        self.assertIn('meter_unit', result)
        self.assertIn('model_name', result)
        self.assertIn('serial', result)
        self.assertIn('status', result)


class TestGetSnapshotAt(unittest.TestCase):
    """Tests for get_snapshot_at()."""

    def setUp(self):
        self.conn = db.init_db(':memory:')

    def tearDown(self):
        db.close_db(self.conn)

    def test_exact_match(self):
        db.save_snapshot(self.conn, '10.0.0.1', 100, 50.0, 'cm', 'Zebra', 'SN', 'idle', timestamp='2026-09-17T10:00:00')
        result = db.get_snapshot_at(self.conn, '10.0.0.1', '2026-09-17T10:00:00')
        self.assertIsNotNone(result)
        self.assertEqual(result['labels_total'], 100)

    def test_closest_before(self):
        db.save_snapshot(self.conn, '10.0.0.1', 100, 50.0, 'cm', 'Zebra', 'SN', 'idle', timestamp='2026-09-17T10:00:00')
        db.save_snapshot(self.conn, '10.0.0.1', 200, 100.0, 'cm', 'Zebra', 'SN', 'idle', timestamp='2026-09-17T10:05:00')
        result = db.get_snapshot_at(self.conn, '10.0.0.1', '2026-09-17T10:03:00')
        self.assertEqual(result['labels_total'], 100)

    def test_no_data_returns_none(self):
        result = db.get_snapshot_at(self.conn, '10.0.0.1', '2026-09-17T10:00:00')
        self.assertIsNone(result)


class TestGetShiftDelta(unittest.TestCase):
    """Tests for get_shift_delta()."""

    def setUp(self):
        self.conn = db.init_db(':memory:')

    def tearDown(self):
        db.close_db(self.conn)

    def test_calculates_delta(self):
        db.save_snapshot(self.conn, '10.0.0.1', 100, 50.0, 'cm', 'Zebra', 'SN', 'idle', timestamp='2026-09-17T06:00:00')
        db.save_snapshot(self.conn, '10.0.0.1', 250, 125.0, 'cm', 'Zebra', 'SN', 'idle', timestamp='2026-09-17T14:00:00')
        result = db.get_shift_delta(self.conn, '10.0.0.1', '2026-09-17T06:00:00', '2026-09-17T14:00:00')
        self.assertIsNotNone(result)
        self.assertEqual(result['labels_delta'], 150)
        self.assertEqual(result['meters_delta'], 75.0)

    def test_missing_start_returns_none(self):
        db.save_snapshot(self.conn, '10.0.0.1', 250, 125.0, 'cm', 'Zebra', 'SN', 'idle', timestamp='2026-09-17T14:00:00')
        result = db.get_shift_delta(self.conn, '10.0.0.1', '2026-09-17T06:00:00', '2026-09-17T14:00:00')
        self.assertIsNone(result)

    def test_missing_end_returns_none(self):
        db.save_snapshot(self.conn, '10.0.0.1', 100, 50.0, 'cm', 'Zebra', 'SN', 'idle', timestamp='2026-09-17T06:00:00')
        result = db.get_shift_delta(self.conn, '10.0.0.1', '2026-09-17T06:00:00', '2026-09-17T14:00:00')
        self.assertIsNone(result)

    def test_zero_delta(self):
        db.save_snapshot(self.conn, '10.0.0.1', 100, 50.0, 'cm', 'Zebra', 'SN', 'idle', timestamp='2026-09-17T06:00:00')
        db.save_snapshot(self.conn, '10.0.0.1', 100, 50.0, 'cm', 'Zebra', 'SN', 'idle', timestamp='2026-09-17T14:00:00')
        result = db.get_shift_delta(self.conn, '10.0.0.1', '2026-09-17T06:00:00', '2026-09-17T14:00:00')
        self.assertEqual(result['labels_delta'], 0)
        self.assertEqual(result['meters_delta'], 0.0)

    def test_returns_snapshots(self):
        db.save_snapshot(self.conn, '10.0.0.1', 100, 50.0, 'cm', 'Zebra', 'SN', 'idle', timestamp='2026-09-17T06:00:00')
        db.save_snapshot(self.conn, '10.0.0.1', 250, 125.0, 'cm', 'Zebra', 'SN', 'idle', timestamp='2026-09-17T14:00:00')
        result = db.get_shift_delta(self.conn, '10.0.0.1', '2026-09-17T06:00:00', '2026-09-17T14:00:00')
        self.assertIn('start_snapshot', result)
        self.assertIn('end_snapshot', result)


class TestGetAllPrintersLatest(unittest.TestCase):
    """Tests for get_all_printers_latest()."""

    def setUp(self):
        self.conn = db.init_db(':memory:')

    def tearDown(self):
        db.close_db(self.conn)

    def test_empty_returns_empty(self):
        result = db.get_all_printers_latest(self.conn)
        self.assertEqual(len(result), 0)

    def test_returns_latest_per_printer(self):
        db.save_snapshot(self.conn, '10.0.0.1', 100, 50.0, 'cm', 'Zebra', 'SN1', 'idle', timestamp='2026-09-17T10:00:00')
        db.save_snapshot(self.conn, '10.0.0.1', 200, 100.0, 'cm', 'Zebra', 'SN1', 'idle', timestamp='2026-09-17T10:05:00')
        db.save_snapshot(self.conn, '10.0.0.2', 300, 150.0, 'cm', 'Zebra', 'SN2', 'idle', timestamp='2026-09-17T10:00:00')
        result = db.get_all_printers_latest(self.conn)
        self.assertEqual(len(result), 2)
        labels_by_ip = {r['printer_ip']: r['labels_total'] for r in result}
        self.assertEqual(labels_by_ip['10.0.0.1'], 200)
        self.assertEqual(labels_by_ip['10.0.0.2'], 300)


class TestGetPrintersByShift(unittest.TestCase):
    """Tests for get_printers_by_shift()."""

    def setUp(self):
        self.conn = db.init_db(':memory:')

    def tearDown(self):
        db.close_db(self.conn)

    def test_returns_deltas_for_all_printers(self):
        db.save_snapshot(self.conn, '10.0.0.1', 100, 50.0, 'cm', 'Zebra', 'SN1', 'idle', timestamp='2026-09-17T06:00:00')
        db.save_snapshot(self.conn, '10.0.0.1', 200, 100.0, 'cm', 'Zebra', 'SN1', 'idle', timestamp='2026-09-17T14:00:00')
        db.save_snapshot(self.conn, '10.0.0.2', 300, 150.0, 'cm', 'Zebra', 'SN2', 'idle', timestamp='2026-09-17T06:00:00')
        db.save_snapshot(self.conn, '10.0.0.2', 400, 200.0, 'cm', 'Zebra', 'SN2', 'idle', timestamp='2026-09-17T14:00:00')
        result = db.get_printers_by_shift(self.conn, '2026-09-17T06:00:00', '2026-09-17T14:00:00')
        self.assertEqual(len(result), 2)

    def test_printer_without_data_excluded(self):
        db.save_snapshot(self.conn, '10.0.0.1', 100, 50.0, 'cm', 'Zebra', 'SN1', 'idle', timestamp='2026-09-17T06:00:00')
        db.save_snapshot(self.conn, '10.0.0.1', 200, 100.0, 'cm', 'Zebra', 'SN1', 'idle', timestamp='2026-09-17T14:00:00')
        result = db.get_printers_by_shift(self.conn, '2026-09-17T06:00:00', '2026-09-17T14:00:00')
        self.assertEqual(len(result), 1)


class TestSaveAndGetPrinterUnit(unittest.TestCase):
    """Tests for save_printer_unit() and get_printer_unit()."""

    def setUp(self):
        self.conn = db.init_db(':memory:')

    def tearDown(self):
        db.close_db(self.conn)

    def test_save_and_get(self):
        db.save_printer_unit(self.conn, '10.0.0.1', 'cm')
        result = db.get_printer_unit(self.conn, '10.0.0.1')
        self.assertEqual(result, 'cm')

    def test_get_nonexistent_returns_none(self):
        result = db.get_printer_unit(self.conn, '10.0.0.1')
        self.assertIsNone(result)

    def test_overwrite_existing(self):
        db.save_printer_unit(self.conn, '10.0.0.1', 'cm')
        db.save_printer_unit(self.conn, '10.0.0.1', 'mm')
        result = db.get_printer_unit(self.conn, '10.0.0.1')
        self.assertEqual(result, 'mm')

    def test_different_printers(self):
        db.save_printer_unit(self.conn, '10.0.0.1', 'cm')
        db.save_printer_unit(self.conn, '10.0.0.2', 'mm')
        self.assertEqual(db.get_printer_unit(self.conn, '10.0.0.1'), 'cm')
        self.assertEqual(db.get_printer_unit(self.conn, '10.0.0.2'), 'mm')


class TestGetHistory(unittest.TestCase):
    """Tests for get_history()."""

    def setUp(self):
        self.conn = db.init_db(':memory:')

    def tearDown(self):
        db.close_db(self.conn)

    def test_returns_all_history(self):
        db.save_snapshot(self.conn, '10.0.0.1', 100, 50.0, 'cm', 'Zebra', 'SN', 'idle', timestamp='2026-09-17T10:00:00')
        db.save_snapshot(self.conn, '10.0.0.1', 200, 100.0, 'cm', 'Zebra', 'SN', 'idle', timestamp='2026-09-17T10:05:00')
        result = db.get_history(self.conn, '10.0.0.1')
        self.assertEqual(len(result), 2)

    def test_date_filter(self):
        db.save_snapshot(self.conn, '10.0.0.1', 100, 50.0, 'cm', 'Zebra', 'SN', 'idle', timestamp='2026-09-16T10:00:00')
        db.save_snapshot(self.conn, '10.0.0.1', 200, 100.0, 'cm', 'Zebra', 'SN', 'idle', timestamp='2026-09-17T10:00:00')
        result = db.get_history(self.conn, '10.0.0.1', start_date='2026-09-17')
        self.assertEqual(len(result), 1)

    def test_end_date_filter(self):
        db.save_snapshot(self.conn, '10.0.0.1', 100, 50.0, 'cm', 'Zebra', 'SN', 'idle', timestamp='2026-09-16T10:00:00')
        db.save_snapshot(self.conn, '10.0.0.1', 200, 100.0, 'cm', 'Zebra', 'SN', 'idle', timestamp='2026-09-17T10:00:00')
        result = db.get_history(self.conn, '10.0.0.1', end_date='2026-09-16')
        self.assertEqual(len(result), 1)

    def test_empty_history(self):
        result = db.get_history(self.conn, '10.0.0.1')
        self.assertEqual(len(result), 0)

    def test_chronological_order(self):
        db.save_snapshot(self.conn, '10.0.0.1', 200, 100.0, 'cm', 'Zebra', 'SN', 'idle', timestamp='2026-09-17T10:05:00')
        db.save_snapshot(self.conn, '10.0.0.1', 100, 50.0, 'cm', 'Zebra', 'SN', 'idle', timestamp='2026-09-17T10:00:00')
        result = db.get_history(self.conn, '10.0.0.1')
        self.assertEqual(result[0]['labels_total'], 100)
        self.assertEqual(result[1]['labels_total'], 200)


class TestRoundTimestamp(unittest.TestCase):
    """Tests for _round_timestamp()."""

    def test_rounds_down(self):
        dt = datetime(2026, 9, 17, 10, 7, 30)
        result = db._round_timestamp(dt)
        self.assertEqual(result, '2026-09-17T10:05:00')

    def test_rounds_exact(self):
        dt = datetime(2026, 9, 17, 10, 5, 0)
        result = db._round_timestamp(dt)
        self.assertEqual(result, '2026-09-17T10:05:00')

    def test_rounds_up(self):
        dt = datetime(2026, 9, 17, 10, 3, 0)
        result = db._round_timestamp(dt)
        self.assertEqual(result, '2026-09-17T10:00:00')

    def test_rounds_to_10_minutes(self):
        dt = datetime(2026, 9, 17, 10, 12, 0)
        result = db._round_timestamp(dt, interval_minutes=10)
        self.assertEqual(result, '2026-09-17T10:10:00')


class TestRowToDict(unittest.TestCase):
    """Tests for _row_to_dict()."""

    def test_converts_tuple(self):
        row = ('10.0.0.1', '2026-09-17T10:00:00', 100, 50.5, 'cm', 'Zebra', 'SN', 'idle')
        result = db._row_to_dict(row)
        self.assertEqual(result['printer_ip'], '10.0.0.1')
        self.assertEqual(result['timestamp'], '2026-09-17T10:00:00')
        self.assertEqual(result['labels_total'], 100)
        self.assertEqual(result['meters_total'], 50.5)
        self.assertEqual(result['meter_unit'], 'cm')
        self.assertEqual(result['model_name'], 'Zebra')
        self.assertEqual(result['serial'], 'SN')
        self.assertEqual(result['status'], 'idle')

    def test_none_values(self):
        row = ('10.0.0.1', '2026-09-17T10:00:00', None, None, None, None, None, None)
        result = db._row_to_dict(row)
        self.assertIsNone(result['labels_total'])
        self.assertIsNone(result['meters_total'])


class TestCloseDb(unittest.TestCase):
    """Tests for close_db()."""

    def test_close_existing(self):
        conn = db.init_db(':memory:')
        db.close_db(conn)  # Should not raise

    def test_close_none(self):
        db.close_db(None)  # Should not raise


if __name__ == '__main__':
    unittest.main()
