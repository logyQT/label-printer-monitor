"""Tests for report.py - CSV report generation.

Tests cover:
- Config loading
- Report generation
- Shift detection in timestamps
- Summary output
"""

import csv
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
import report


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


class TestGetShiftForTime(unittest.TestCase):
    """Tests for _get_shift_for_time()."""

    def setUp(self):
        self.shifts = [
            {"name": "Morning", "start": "06:00", "end": "14:00"},
            {"name": "Afternoon", "start": "14:00", "end": "22:00"},
            {"name": "Night", "start": "22:00", "end": "06:00"},
        ]

    def test_morning_shift(self):
        result = report._get_shift_for_time('2026-09-17T10:00:00', self.shifts)
        self.assertEqual(result, 'Morning')

    def test_afternoon_shift(self):
        result = report._get_shift_for_time('2026-09-17T16:00:00', self.shifts)
        self.assertEqual(result, 'Afternoon')

    def test_night_shift(self):
        result = report._get_shift_for_time('2026-09-17T23:00:00', self.shifts)
        self.assertEqual(result, 'Night')

    def test_unknown_time(self):
        result = report._get_shift_for_time('invalid', self.shifts)
        self.assertEqual(result, 'Unknown')

    def test_empty_shifts(self):
        result = report._get_shift_for_time('2026-09-17T10:00:00', [])
        self.assertEqual(result, 'Unknown')


class TestGenerateWeeklyReport(unittest.TestCase):
    """Tests for generate_weekly_report()."""

    def setUp(self):
        self.config = {
            'db_path': ':memory:',
            'printers': [
                {'ip': '10.0.0.1', 'model': 'Zebra ZT230', 'location': 'Line 1'},
            ],
            'shifts': [
                {"name": "Morning", "start": "06:00", "end": "14:00"},
            ],
        }

    def test_generates_csv_file(self):
        conn = db.init_db(':memory:')
        db.save_snapshot(conn, '10.0.0.1', 100, 50.0, 'cm', 'Zebra ZT230', timestamp='2026-09-17T10:00:00')
        db.close_db(conn)

        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = os.path.join(tmpdir, 'test_report.csv')
            # Mock the report path
            with patch('report.os.path.join', return_value=report_path):
                result = report.generate_weekly_report(
                    self.config, '2026-09-10', '2026-09-17'
                )
                self.assertTrue(os.path.exists(report_path))

    def test_csv_has_headers(self):
        conn = db.init_db(':memory:')
        db.save_snapshot(conn, '10.0.0.1', 100, 50.0, 'cm', 'Zebra ZT230', timestamp='2026-09-17T10:00:00')
        db.close_db(conn)

        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = os.path.join(tmpdir, 'test_report.csv')
            with patch('report.os.path.join', return_value=report_path):
                report.generate_weekly_report(self.config, '2026-09-10', '2026-09-17')
                with open(report_path, 'r') as f:
                    reader = csv.reader(f)
                    headers = next(reader)
                    self.assertIn('IP Address', headers)
                    self.assertIn('Location', headers)
                    self.assertIn('Labels Total', headers)
                    self.assertIn('Meters Total', headers)


class TestWriteSummaryRows(unittest.TestCase):
    """Tests for _write_summary_rows()."""

    def test_writes_rows(self):
        import io
        output = io.StringIO()
        writer = csv.writer(output)
        history = [
            {
                'printer_ip': '10.0.0.1',
                'timestamp': '2026-09-17T10:00:00',
                'labels_total': 100,
                'meters_total': 50.0,
                'meter_unit': 'cm',
            }
        ]
        report._write_summary_rows(writer, '10.0.0.1', 'Line 1', 'Zebra ZT230', 'ABC123', history)
        lines = output.getvalue().strip().split('\n')
        self.assertEqual(len(lines), 1)
        self.assertIn('10.0.0.1', lines[0])
        self.assertIn('Line 1', lines[0])


class TestWriteShiftRows(unittest.TestCase):
    """Tests for _write_shift_rows()."""

    def setUp(self):
        self.config = {
            'shifts': [
                {"name": "Morning", "start": "06:00", "end": "14:00"},
            ],
        }

    def test_writes_rows_with_shift(self):
        import io
        output = io.StringIO()
        writer = csv.writer(output)
        history = [
            {
                'printer_ip': '10.0.0.1',
                'timestamp': '2026-09-17T10:00:00',
                'labels_total': 100,
                'meters_total': 50.0,
                'meter_unit': 'cm',
            }
        ]
        report._write_shift_rows(writer, '10.0.0.1', 'Line 1', 'Zebra ZT230', 'ABC123', history, self.config)
        lines = output.getvalue().strip().split('\n')
        self.assertEqual(len(lines), 1)
        self.assertIn('Morning', lines[0])


class TestGenerateSummary(unittest.TestCase):
    """Tests for generate_summary()."""

    def test_prints_summary(self):
        config = {
            'db_path': ':memory:',
            'printers': [
                {'ip': '10.0.0.1', 'model': 'Zebra ZT230', 'location': 'Line 1'},
            ],
        }
        conn = db.init_db(':memory:')
        db.save_snapshot(conn, '10.0.0.1', 100, 50.0, 'cm', 'Zebra ZT230', timestamp='2026-09-17T10:00:00')
        db.close_db(conn)

        with patch('builtins.print') as mock_print:
            report.generate_summary(config, '2026-09-10', '2026-09-17')
            # Should have printed header, separator, data, separator, total
            self.assertTrue(mock_print.called)


class TestMainFunction(unittest.TestCase):
    """Tests for main() argument parsing."""

    def test_no_args_generates_report(self):
        with patch('report.load_config') as mock_config:
            mock_config.return_value = {
                'db_path': ':memory:',
                'printers': [],
            }
            with patch('report.generate_weekly_report') as mock_report:
                mock_report.return_value = 'report.csv'
                with patch('sys.argv', ['report.py']):
                    report.main()
                mock_report.assert_called_once()

    def test_summary_flag(self):
        with patch('report.load_config') as mock_config:
            mock_config.return_value = {
                'db_path': ':memory:',
                'printers': [],
            }
            with patch('report.generate_summary') as mock_summary:
                with patch('sys.argv', ['report.py', '--summary']):
                    report.main()
                mock_summary.assert_called_once()

    def test_shifts_flag(self):
        with patch('report.load_config') as mock_config:
            mock_config.return_value = {
                'db_path': ':memory:',
                'printers': [],
            }
            with patch('report.generate_weekly_report') as mock_report:
                mock_report.return_value = 'report.csv'
                with patch('sys.argv', ['report.py', '--shifts']):
                    report.main()
                args = mock_report.call_args
                self.assertTrue(args[1].get('include_shifts', False) or args[0][3] if len(args[0]) > 3 else False)


if __name__ == '__main__':
    unittest.main()
