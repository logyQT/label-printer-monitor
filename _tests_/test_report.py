"""Tests for report.py — shift-based printer statistics.

Tests cover:
- Config loading
- Epoch / shift helper functions
- Unit conversion
- compute_shift_deltas (with mocked DB)
- print_report (stdout output)
- export_csv (file output)
"""

import csv
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
import report


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config():
    """Return a minimal config dict for tests."""
    return {
        'db_path': ':memory:',
        'printers': [
            {'ip': '10.0.0.1', 'model': 'Zebra ZT230', 'location': 'Line 1'},
            {'ip': '10.0.0.2', 'model': 'Sato CL4NX Plus', 'location': 'Line 2'},
        ],
        'shifts': [
            {'name': 'Morning', 'start': '06:00', 'end': '14:00'},
            {'name': 'Afternoon', 'start': '14:00', 'end': '22:00'},
        ],
    }


def _seed_snapshots(conn, printer_ip, timestamps_labels_meters_unit):
    """Insert snapshot rows from a list of (epoch, labels, meters, unit)."""
    for ts, labels, meters, unit in timestamps_labels_meters_unit:
        db.save_snapshot(conn, printer_ip, labels, meters, unit,
                         model_name='TestModel', timestamp=ts)


def _epoch_for_date(date_str, hour, minute=0):
    """Return epoch int for a date+time (UTC)."""
    dt = datetime.fromisoformat(f'{date_str}T{hour:02d}:{minute:02d}:00+00:00')
    return int(dt.timestamp())


# ---------------------------------------------------------------------------
# Tests for load_config
# ---------------------------------------------------------------------------

class TestLoadConfig(unittest.TestCase):
    """Tests for load_config()."""

    def test_load_valid_config(self):
        config = report.load_config('config.json')
        self.assertIn('printers', config)
        self.assertIn('db_path', config)

    def test_missing_config_exits(self):
        with self.assertRaises(SystemExit) as ctx:
            report.load_config('nonexistent.json')
        self.assertEqual(ctx.exception.code, 2)


# ---------------------------------------------------------------------------
# Tests for _to_epoch
# ---------------------------------------------------------------------------

class TestToEpoch(unittest.TestCase):
    """Tests for _to_epoch()."""

    def test_known_date(self):
        result = report._to_epoch('2026-09-17')
        # Should be a reasonable epoch for 2026-09-17 00:00:00 UTC
        self.assertIsInstance(result, int)
        self.assertGreater(result, 0)

    def test_returns_int(self):
        result = report._to_epoch('2020-01-01')
        self.assertIsInstance(result, int)


# ---------------------------------------------------------------------------
# Tests for _epoch_to_shift
# ---------------------------------------------------------------------------

class TestEpochToShift(unittest.TestCase):
    """Tests for _epoch_to_shift()."""

    def setUp(self):
        self.shifts = [
            {'name': 'Morning', 'start': '06:00', 'end': '14:00'},
            {'name': 'Afternoon', 'start': '14:00', 'end': '22:00'},
        ]

    def test_morning_shift(self):
        epoch = _epoch_for_date('2026-09-17', 10, 0)
        self.assertEqual(report._epoch_to_shift(epoch, self.shifts), 'Morning')

    def test_afternoon_shift(self):
        epoch = _epoch_for_date('2026-09-17', 16, 0)
        self.assertEqual(report._epoch_to_shift(epoch, self.shifts), 'Afternoon')

    def test_no_matching_shift(self):
        epoch = _epoch_for_date('2026-09-17', 3, 0)  # 03:00 — no shift
        self.assertIsNone(report._epoch_to_shift(epoch, self.shifts))

    def test_empty_shifts(self):
        epoch = _epoch_for_date('2026-09-17', 10, 0)
        self.assertIsNone(report._epoch_to_shift(epoch, []))

    def test_overnight_shift(self):
        shifts = [{'name': 'Night', 'start': '22:00', 'end': '06:00'}]
        epoch = _epoch_for_date('2026-09-17', 23, 0)
        self.assertEqual(report._epoch_to_shift(epoch, shifts), 'Night')


# ---------------------------------------------------------------------------
# Tests for _convert_to_meters
# ---------------------------------------------------------------------------

class TestConvertToMeters(unittest.TestCase):
    """Tests for _convert_to_meters()."""

    def test_cm_to_m(self):
        self.assertAlmostEqual(report._convert_to_meters(100, 'cm'), 1.0)

    def test_m_passthrough(self):
        self.assertAlmostEqual(report._convert_to_meters(5.5, 'm'), 5.5)

    def test_in_to_m(self):
        self.assertAlmostEqual(report._convert_to_meters(1, 'in'), 0.0254)

    def test_mm_to_m(self):
        self.assertAlmostEqual(report._convert_to_meters(1000, 'mm'), 1.0)

    def test_none_returns_none(self):
        self.assertIsNone(report._convert_to_meters(None, 'cm'))

    def test_unknown_unit_passthrough(self):
        self.assertAlmostEqual(report._convert_to_meters(42, 'ft'), 42)


# ---------------------------------------------------------------------------
# Tests for compute_shift_deltas
# ---------------------------------------------------------------------------

class TestComputeShiftDeltas(unittest.TestCase):
    """Tests for compute_shift_deltas() with real in-memory DB."""

    def test_basic_morning_shift(self):
        """Two snapshots in the Morning window should produce a delta."""
        config = _make_config()
        conn = db.init_db(':memory:')

        # 2026-09-17: Morning shift = 06:00–14:00 UTC
        ts_start = _epoch_for_date('2026-09-17', 7, 0)
        ts_end = _epoch_for_date('2026-09-17', 12, 0)
        db.save_snapshot(conn, '10.0.0.1', 100, 50.0, 'cm', 'Zebra ZT230',
                         timestamp=ts_start)
        db.save_snapshot(conn, '10.0.0.1', 120, 60.0, 'cm', 'Zebra ZT230',
                         timestamp=ts_end)

        # Patch to return our still-open connection
        with patch('report.db.init_db', return_value=conn), \
             patch('report.db.close_db'):
            results = report.compute_shift_deltas(config, '2026-09-17', '2026-09-17')

        db.close_db(conn)
        self.assertTrue(len(results) >= 1)
        zebras = [r for r in results if r['ip'] == '10.0.0.1']
        self.assertTrue(len(zebras) >= 1)
        r = zebras[0]
        self.assertEqual(r['shift'], 'Morning')
        self.assertEqual(r['labels_delta'], 20)
        self.assertAlmostEqual(r['meters_delta'], 0.1)  # 10 cm = 0.1 m

    def test_no_snapshots_returns_empty(self):
        """No snapshots in DB → empty results."""
        config = _make_config()
        conn = db.init_db(':memory:')

        with patch('report.db.init_db', return_value=conn), \
             patch('report.db.close_db'):
            results = report.compute_shift_deltas(config, '2026-09-17', '2026-09-17')

        db.close_db(conn)
        self.assertEqual(results, [])

    def test_multiple_printers(self):
        """Snapshots for two printers should yield rows for each."""
        config = _make_config()
        conn = db.init_db(':memory:')

        for ip in ('10.0.0.1', '10.0.0.2'):
            db.save_snapshot(conn, ip, 10, 1.0, 'm', 'Model',
                             timestamp=_epoch_for_date('2026-09-17', 8, 0))
            db.save_snapshot(conn, ip, 20, 2.0, 'm', 'Model',
                             timestamp=_epoch_for_date('2026-09-17', 10, 0))

        with patch('report.db.init_db', return_value=conn), \
             patch('report.db.close_db'):
            results = report.compute_shift_deltas(config, '2026-09-17', '2026-09-17')

        db.close_db(conn)
        ips = {r['ip'] for r in results}
        self.assertIn('10.0.0.1', ips)
        self.assertIn('10.0.0.2', ips)

    def test_multiple_days(self):
        """Snapshots on two consecutive days produce rows for each day."""
        config = _make_config()
        conn = db.init_db(':memory:')

        for day in ('2026-09-17', '2026-09-18'):
            db.save_snapshot(conn, '10.0.0.1', 50, 5.0, 'm', 'Zebra ZT230',
                             timestamp=_epoch_for_date(day, 8, 0))
            db.save_snapshot(conn, '10.0.0.1', 60, 6.0, 'm', 'Zebra ZT230',
                             timestamp=_epoch_for_date(day, 10, 0))

        with patch('report.db.init_db', return_value=conn), \
             patch('report.db.close_db'):
            results = report.compute_shift_deltas(config, '2026-09-17', '2026-09-18')

        db.close_db(conn)
        dates = {r['date'] for r in results if r['ip'] == '10.0.0.1'}
        self.assertIn('2026-09-17', dates)
        self.assertIn('2026-09-18', dates)

    def test_single_snapshot_skipped(self):
        """A single snapshot in a shift produces no delta (needs >= 2)."""
        config = _make_config()
        conn = db.init_db(':memory:')

        db.save_snapshot(conn, '10.0.0.1', 100, 50.0, 'cm', 'Zebra ZT230',
                         timestamp=_epoch_for_date('2026-09-17', 9, 0))

        with patch('report.db.init_db', return_value=conn), \
             patch('report.db.close_db'):
            results = report.compute_shift_deltas(config, '2026-09-17', '2026-09-17')

        db.close_db(conn)
        self.assertEqual(results, [])


# ---------------------------------------------------------------------------
# Tests for print_report
# ---------------------------------------------------------------------------

class TestPrintReport(unittest.TestCase):
    """Tests for print_report()."""

    def _get_printed_text(self, mock_print):
        """Collect all printed text from a mock print."""
        lines = []
        for c in mock_print.call_args_list:
            if c.args:
                lines.append(str(c.args[0]))
        return '\n'.join(lines)

    def test_prints_zebra_table(self):
        config = _make_config()
        results = [
            {'ip': '10.0.0.1', 'model': 'Zebra ZT230', 'shift': 'Morning',
             'date': '2026-09-17', 'labels_delta': 150, 'meters_delta': 0.5},
        ]
        with patch('builtins.print') as mock_print:
            report.print_report(results, config)
        text = self._get_printed_text(mock_print)
        self.assertIn('ZEBRA', text)
        self.assertIn('10.0.0.1', text)
        self.assertIn('150', text)

    def test_prints_sato_table(self):
        config = _make_config()
        results = [
            {'ip': '10.0.0.2', 'model': 'Sato CL4NX Plus', 'shift': 'Morning',
             'date': '2026-09-17', 'labels_delta': None, 'meters_delta': 3.2},
        ]
        with patch('builtins.print') as mock_print:
            report.print_report(results, config)
        text = self._get_printed_text(mock_print)
        self.assertIn('SATO', text)
        self.assertIn('3.2', text)

    def test_empty_results(self):
        config = _make_config()
        with patch('builtins.print') as mock_print:
            report.print_report([], config)
        text = self._get_printed_text(mock_print)
        self.assertIn('No shift data', text)


# ---------------------------------------------------------------------------
# Tests for export_csv
# ---------------------------------------------------------------------------

class TestExportCsv(unittest.TestCase):
    """Tests for export_csv()."""

    def test_creates_csv_with_headers(self):
        config = _make_config()
        results = [
            {'ip': '10.0.0.1', 'model': 'Zebra ZT230', 'shift': 'Morning',
             'date': '2026-09-17', 'labels_delta': 100, 'meters_delta': 1.5},
            {'ip': '10.0.0.2', 'model': 'Sato CL4NX Plus', 'shift': 'Morning',
             'date': '2026-09-17', 'labels_delta': None, 'meters_delta': 3.0},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, 'report.csv')
            report.export_csv(results, config, csv_path)
            self.assertTrue(os.path.exists(csv_path))
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                headers = next(reader)
                self.assertEqual(headers,
                                 ['IP', 'Model', 'Shift', 'Date',
                                  'Labels Delta', 'Meters Delta (m)'])

    def test_csv_row_count(self):
        config = _make_config()
        results = [
            {'ip': '10.0.0.1', 'model': 'Zebra ZT230', 'shift': 'Morning',
             'date': '2026-09-17', 'labels_delta': 50, 'meters_delta': 0.5},
            {'ip': '10.0.0.1', 'model': 'Zebra ZT230', 'shift': 'Afternoon',
             'date': '2026-09-17', 'labels_delta': 70, 'meters_delta': 0.8},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, 'report.csv')
            report.export_csv(results, config, csv_path)
            with open(csv_path, 'r', encoding='utf-8') as f:
                lines = f.read().strip().split('\n')
                # header + 2 data rows
                self.assertEqual(len(lines), 3)

    def test_none_values_written_as_empty(self):
        config = _make_config()
        results = [
            {'ip': '10.0.0.1', 'model': 'Zebra ZT230', 'shift': 'Morning',
             'date': '2026-09-17', 'labels_delta': None, 'meters_delta': None},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, 'report.csv')
            report.export_csv(results, config, csv_path)
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                next(reader)  # skip header
                row = next(reader)
                # labels and meters columns should be empty strings
                self.assertEqual(row[4], '')
                self.assertEqual(row[5], '')


# ---------------------------------------------------------------------------
# Tests for main() argument parsing
# ---------------------------------------------------------------------------

class TestMainFunction(unittest.TestCase):
    """Tests for main() argument parsing."""

    def test_default_args_prints_report(self):
        with patch('report.load_config') as mock_config:
            mock_config.return_value = {
                'db_path': ':memory:',
                'printers': [],
                'shifts': [],
            }
            with patch('report.compute_shift_deltas', return_value=[]) as mock_compute, \
                 patch('report.print_report') as mock_print, \
                 patch('sys.argv', ['report.py']):
                report.main()
            mock_compute.assert_called_once()
            mock_print.assert_called_once()

    def test_csv_flag_exports(self):
        with patch('report.load_config') as mock_config:
            mock_config.return_value = {
                'db_path': ':memory:',
                'printers': [],
                'shifts': [],
            }
            with patch('report.compute_shift_deltas', return_value=[]) as mock_compute, \
                 patch('report.export_csv') as mock_export, \
                 patch('sys.argv', ['report.py', '--csv']):
                report.main()
            mock_compute.assert_called_once()
            mock_export.assert_called_once()

    def test_date_range_passed_through(self):
        with patch('report.load_config') as mock_config:
            mock_config.return_value = {
                'db_path': ':memory:',
                'printers': [],
                'shifts': [],
            }
            with patch('report.compute_shift_deltas', return_value=[]) as mock_compute, \
                 patch('report.print_report'), \
                 patch('sys.argv', ['report.py', '--from', '2026-09-10', '--to', '2026-09-15']):
                report.main()
            mock_compute.assert_called_once_with(mock_config.return_value, '2026-09-10', '2026-09-15')


if __name__ == '__main__':
    unittest.main()
