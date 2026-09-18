"""Tests for main.py - entry point and executor.

Tests cover:
- Config loading
- Logging setup
- Collection logic
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # src/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # project root

import db
import main


class TestLoadConfig(unittest.TestCase):
    """Tests for load_config()."""

    def test_load_valid_config(self):
        config = main.load_config()
        self.assertIn('printers', config)
        self.assertIn('snmp', config)
        self.assertIn('db', config)

    def test_load_config_with_printers(self):
        config = main.load_config()
        self.assertGreater(len(config['printers']), 0)
        printer = config['printers'][0]
        self.assertIn('ip', printer)
        self.assertIn('model', printer)
        self.assertIn('location', printer)


class TestSetupLogging(unittest.TestCase):
    """Tests for setup_logging()."""

    def _close_logging_handlers(self):
        """Close logging handlers so files are not locked on Windows."""
        import logging
        logger = logging.getLogger('printer_stats')
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)

    def test_creates_log_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = os.path.join(tmpdir, 'test_logs')
            log_file = main.setup_logging(log_dir)
            self.assertTrue(os.path.exists(log_dir))
            self.assertTrue(os.path.exists(log_file))
            self._close_logging_handlers()

    def test_log_file_name_format(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = main.setup_logging(tmpdir)
            basename = os.path.basename(log_file)
            self.assertTrue(basename.startswith('run_'))
            self.assertTrue(basename.endswith('.log'))
            self._close_logging_handlers()


class TestCollectPrinter(unittest.TestCase):
    """Tests for collect_printer()."""

    def test_successful_collection(self):
        adapter = MagicMock()
        adapter.get_counters.return_value = {
            'labels_total': 100,
            'meters_total': 50.0,
            'meter_unit': 'cm',
            'model_name': 'Zebra ZT230',
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
            'db': {'filename': ':memory:'},
            'log_dir': tempfile.mkdtemp(),
            'snmp': {'community': 'public', 'timeout_sec': 1, 'retries': 0},
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
            'reachable': False,
        }
        mock_create.return_value = adapter
        success, fail, total = main.run_collection(self.config)
        self.assertEqual(success, 0)
        self.assertEqual(fail, 1)


if __name__ == '__main__':
    unittest.main()
