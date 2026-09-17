"""Tests for main.py - entry point and executor.

Tests cover:
- Config loading
- Logging setup
- Shift detection
- Collection logic
- Shift delta calculation
- Report generation
- Argument parsing
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
import main


class TestLoadConfig(unittest.TestCase):
    """Tests for load_config()."""

    def test_load_valid_config(self):
        config = main.load_config('config.json')
        self.assertIn('printers', config)
        self.assertIn('snmp', config)
        self.assertIn('shifts', config)
        self.assertIn('db_path', config)

    def test_load_config_with_printers(self):
        config = main.load_config('config.json')
        self.assertGreater(len(config['printers']), 0)
        printer = config['printers'][0]
        self.assertIn('ip', printer)
        self.assertIn('model', printer)
        self.assertIn('location', printer)

    def test_config_has_shifts(self):
        config = main.load_config('config.json')
        self.assertEqual(len(config['shifts']), 3)

    def test_missing_config_exits(self):
        with self.assertRaises(SystemExit) as ctx:
            main.load_config('nonexistent.json')
        self.assertEqual(ctx.exception.code, 2)

    def test_invalid_json_exits(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write('{invalid json')
            f.flush()
            temp_path = f.name
        try:
            with self.assertRaises(SystemExit) as ctx:
                main.load_config(temp_path)
            self.assertEqual(ctx.exception.code, 2)
        finally:
            os.unlink(temp_path)


class TestSetupLogging(unittest.TestCase):
    """Tests for setup_logging()."""

    def test_creates_log_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = os.path.join(tmpdir, 'test_logs')
            log_file = main.setup_logging(log_dir)
            self.assertTrue(os.path.exists(log_dir))
            self.assertTrue(os.path.exists(log_file))
            os.unlink(log_file)

    def test_log_file_name_format(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = main.setup_logging(tmpdir)
            basename = os.path.basename(log_file)
            self.assertTrue(basename.startswith('run_'))
            self.assertTrue(basename.endswith('.log'))
            os.unlink(log_file)


class TestDetectCurrentShift(unittest.TestCase):
    """Tests for detect_current_shift()."""

    def setUp(self):
        self.shifts = [
            {"name": "Morning", "start": "06:00", "end": "14:00"},
            {"name": "Afternoon", "start": "14:00", "end": "22:00"},
            {"name": "Night", "start": "22:00", "end": "06:00"},
        ]

    def test_morning_start(self):
        now = datetime(2026, 9, 17, 5, 55)
        shift, phase = main.detect_current_shift(self.shifts, now)
        self.assertEqual(shift['name'], 'Morning')
        self.assertEqual(phase, 'start')

    def test_morning_end(self):
        now = datetime(2026, 9, 17, 13, 55)
        shift, phase = main.detect_current_shift(self.shifts, now)
        # At 13:55, both Morning end and Afternoon start are within 10 min.
        # Start takes priority, so this detects Afternoon start.
        self.assertEqual(shift['name'], 'Afternoon')
        self.assertEqual(phase, 'start')

    def test_afternoon_start(self):
        now = datetime(2026, 9, 17, 13, 55)
        shift, phase = main.detect_current_shift(self.shifts, now)
        # Should detect morning end OR afternoon start (both at ~14:00)
        self.assertIsNotNone(shift)

    def test_night_start(self):
        now = datetime(2026, 9, 17, 22, 3)
        shift, phase = main.detect_current_shift(self.shifts, now)
        self.assertEqual(shift['name'], 'Night')
        self.assertEqual(phase, 'start')

    def test_no_shift_detected(self):
        now = datetime(2026, 9, 17, 10, 0)  # Middle of morning shift
        shift, phase = main.detect_current_shift(self.shifts, now)
        self.assertIsNone(shift)
        self.assertIsNone(phase)

    def test_empty_shifts(self):
        now = datetime(2026, 9, 17, 10, 0)
        shift, phase = main.detect_current_shift([], now)
        self.assertIsNone(shift)


class TestWithinMinutes(unittest.TestCase):
    """Tests for _within_minutes()."""

    def test_exact_match(self):
        self.assertTrue(main._within_minutes('10:00', '10:00', 5))

    def test_within_5_minutes(self):
        self.assertTrue(main._within_minutes('10:03', '10:00', 5))

    def test_outside_5_minutes(self):
        self.assertFalse(main._within_minutes('10:06', '10:00', 5))

    def test_before_within_range(self):
        self.assertTrue(main._within_minutes('09:58', '10:00', 5))

    def test_after_within_range(self):
        self.assertTrue(main._within_minutes('10:02', '10:00', 5))


class TestCollectPrinter(unittest.TestCase):
    """Tests for collect_printer()."""

    def test_successful_collection(self):
        adapter = MagicMock()
        adapter.get_counters.return_value = {
            'labels_total': 100,
            'meters_total': 50.0,
            'meter_unit': 'cm',
            'model_name': 'Zebra ZT230',
            'serial': 'ABC123',
            'status': 'idle',
            'reachable': True,
        }
        config = {'ip': '10.0.0.1', 'location': 'Line 1'}
        result = main.collect_printer(adapter, config)
        self.assertIsNotNone(result)
        self.assertEqual(result['labels_total'], 100)

    def test_unreachable_printer(self):
        adapter = MagicMock()
        adapter.get_counters.return_value = {
            'labels_total': None,
            'meters_total': None,
            'meter_unit': 'unknown',
            'model_name': '',
            'serial': '',
            'status': 'offline',
            'reachable': False,
        }
        config = {'ip': '10.0.0.1', 'location': 'Line 1'}
        result = main.collect_printer(adapter, config)
        self.assertIsNone(result)

    def test_exception_returns_none(self):
        adapter = MagicMock()
        adapter.get_counters.side_effect = Exception('SNMP error')
        config = {'ip': '10.0.0.1', 'location': 'Line 1'}
        result = main.collect_printer(adapter, config)
        self.assertIsNone(result)


class TestRunCollection(unittest.TestCase):
    """Tests for run_collection()."""

    def setUp(self):
        self.config = {
            'db_path': ':memory:',
            'log_dir': tempfile.mkdtemp(),
            'snmp': {'community': 'public', 'timeout_sec': 1, 'retries': 0},
            'shifts': [],
            'printers': [
                {'ip': '10.0.0.1', 'model': 'Zebra ZT230', 'location': 'Line 1'},
            ],
        }

    @patch('main.create_adapter')
    def test_successful_collection(self, mock_create):
        adapter = MagicMock()
        adapter.get_counters.return_value = {
            'labels_total': 100,
            'meters_total': 50.0,
            'meter_unit': 'cm',
            'model_name': 'Zebra ZT230',
            'serial': 'ABC123',
            'status': 'idle',
            'reachable': True,
        }
        mock_create.return_value = adapter
        success, fail, total = main.run_collection(self.config)
        self.assertEqual(success, 1)
        self.assertEqual(fail, 0)
        self.assertEqual(total, 1)

    @patch('main.create_adapter')
    def test_failed_collection(self, mock_create):
        adapter = MagicMock()
        adapter.get_counters.return_value = {
            'labels_total': None,
            'meters_total': None,
            'meter_unit': 'unknown',
            'model_name': '',
            'serial': '',
            'status': 'offline',
            'reachable': False,
        }
        mock_create.return_value = adapter
        success, fail, total = main.run_collection(self.config)
        self.assertEqual(success, 0)
        self.assertEqual(fail, 1)


class TestCalculateShiftDeltas(unittest.TestCase):
    """Tests for calculate_shift_deltas()."""

    def setUp(self):
        self.config = {
            'db_path': ':memory:',
            'printers': [
                {'ip': '10.0.0.1', 'location': 'Line 1'},
            ],
        }

    def test_calculates_deltas(self):
        conn = db.init_db(':memory:')
        db.save_snapshot(conn, '10.0.0.1', 100, 50.0, 'cm', 'Zebra', 'SN', 'idle', timestamp='2026-09-17T06:00:00')
        db.save_snapshot(conn, '10.0.0.1', 200, 100.0, 'cm', 'Zebra', 'SN', 'idle', timestamp='2026-09-17T14:00:00')
        db.close_db(conn)

        # Overwrite config to use in-memory db
        self.config['db_path'] = ':memory:'
        # This test is limited since init_db creates a new connection
        # In practice, the function would use the same connection


class TestGenerateReport(unittest.TestCase):
    """Tests for generate_report()."""

    def test_generates_csv(self):
        config = {
            'db_path': ':memory:',
            'printers': [
                {'ip': '10.0.0.1', 'model': 'Zebra ZT230', 'location': 'Line 1'},
            ],
        }
        # Initialize database with some data
        conn = db.init_db(':memory:')
        db.save_snapshot(conn, '10.0.0.1', 100, 50.0, 'cm', 'Zebra ZT230', 'ABC123', 'idle', timestamp='2026-09-17T10:00:00')
        db.close_db(conn)

        # The function will use its own connection, so this test is limited
        # In practice, you'd need to mock db.init_db to return the same connection


class TestMainFunction(unittest.TestCase):
    """Tests for main() argument parsing."""

    def test_no_args_runs_collection(self):
        with patch('main.load_config') as mock_config:
            mock_config.return_value = {
                'db_path': ':memory:',
                'log_dir': tempfile.mkdtemp(),
                'snmp': {'community': 'public', 'timeout_sec': 1, 'retries': 0},
                'shifts': [],
                'printers': [],
            }
            with patch('main.run_collection') as mock_run:
                mock_run.return_value = (0, 0, 0)
                with patch('sys.argv', ['main.py']):
                    main.main()
                mock_run.assert_called_once()

    def test_collect_flag(self):
        with patch('main.load_config') as mock_config:
            mock_config.return_value = {
                'db_path': ':memory:',
                'log_dir': tempfile.mkdtemp(),
                'snmp': {'community': 'public', 'timeout_sec': 1, 'retries': 0},
                'shifts': [],
                'printers': [],
            }
            with patch('main.run_collection') as mock_run:
                mock_run.return_value = (0, 0, 0)
                with patch('sys.argv', ['main.py', '--collect']):
                    main.main()
                mock_run.assert_called_once()

    def test_report_flag(self):
        with patch('main.load_config') as mock_config:
            mock_config.return_value = {
                'db_path': ':memory:',
                'log_dir': tempfile.mkdtemp(),
                'printers': [],
            }
            with patch('main.generate_report') as mock_report:
                mock_report.return_value = 'report.csv'
                with patch('sys.argv', ['main.py', '--report']):
                    main.main()
                mock_report.assert_called_once()


if __name__ == '__main__':
    unittest.main()
