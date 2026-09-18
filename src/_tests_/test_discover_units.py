"""Tests for discover_units.py - one-time meter unit detection.

Tests cover:
- Config loading/saving
- Printer discovery with mocked SNMP
- Unit detection logic
- Config update
- All-printers discovery
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discover_units
from snmp_client import SnmpTimeout, SnmpError

_DATA_CONFIG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'config', 'config.json',
)


class TestLoadConfig(unittest.TestCase):
    """Tests for load_config()."""

    def test_load_valid_config(self):
        config = discover_units.load_config(_DATA_CONFIG)
        self.assertIn('printers', config)

    def test_missing_config_exits(self):
        with self.assertRaises(SystemExit):
            discover_units.load_config('nonexistent.json')


class TestSaveConfig(unittest.TestCase):
    """Tests for save_config()."""

    def test_saves_config(self):
        config = {'printers': [{'ip': '10.0.0.1', 'meter_unit': 'cm'}]}
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            temp_path = f.name
        try:
            discover_units.save_config(config, temp_path)
            with open(temp_path, 'r') as f:
                loaded = json.load(f)
            self.assertEqual(loaded['printers'][0]['meter_unit'], 'cm')
        finally:
            os.unlink(temp_path)


class TestDiscoverPrinter(unittest.TestCase):
    """Tests for discover_printer() with mocked SNMP."""

    @patch('discover_units.get')
    def test_successful_discovery(self, mock_get):
        def side_effect(ip, oid, community, timeout, retries):
            responses = {
                '1.3.6.1.2.1.1.1.0': (b'Zebra ZT230', 0x04),
                '1.3.6.1.4.1.10642.1.1.0': (b'ZTC ZT230-200dpi ZPL', 0x04),
                '1.3.6.1.4.1.10642.1.9.0': (b'ABC123', 0x04),
                '1.3.6.1.2.1.43.10.2.1.3.1.1': (5, 0x02),
                '1.3.6.1.4.1.10642.20.17.3.0': (1234.5, 0x41),
            }
            return responses.get(oid, (None, None))

        mock_get.side_effect = side_effect
        result = discover_units.discover_printer('10.0.0.1')

        self.assertIsNotNone(result)
        self.assertEqual(result['ip'], '10.0.0.1')
        self.assertEqual(result['model_name'], 'ZTC ZT230-200dpi ZPL')
        self.assertEqual(result['serial'], 'ABC123')
        self.assertEqual(result['unit'], 'linearMeters')
        self.assertTrue(result['meters_available'])

    @patch('discover_units.get')
    def test_unreachable_printer(self, mock_get):
        mock_get.side_effect = SnmpTimeout('timeout')
        result = discover_units.discover_printer('10.0.0.1')
        self.assertIsNone(result)

    @patch('discover_units.get')
    def test_no_unit_oid_with_meters(self, mock_get):
        def side_effect(ip, oid, community, timeout, retries):
            responses = {
                '1.3.6.1.2.1.1.1.0': (b'Zebra', 0x04),
                '1.3.6.1.4.1.10642.1.1.0': (b'Zebra', 0x04),
                '1.3.6.1.4.1.10642.1.9.0': (b'SN', 0x04),
                '1.3.6.1.2.1.43.10.2.1.3.1.1': (None, None),
                '1.3.6.1.4.1.10642.20.17.3.0': (100.0, 0x41),
            }
            return responses.get(oid, (None, None))

        mock_get.side_effect = side_effect
        result = discover_units.discover_printer('10.0.0.1')

        self.assertIsNotNone(result)
        self.assertIn('assumed', result['unit'])

    @patch('discover_units.get')
    def test_no_meters_no_unit(self, mock_get):
        def side_effect(ip, oid, community, timeout, retries):
            responses = {
                '1.3.6.1.2.1.1.1.0': (b'Zebra', 0x04),
                '1.3.6.1.4.1.10642.1.1.0': (b'Zebra', 0x04),
                '1.3.6.1.4.1.10642.1.9.0': (b'SN', 0x04),
                '1.3.6.1.2.1.43.10.2.1.3.1.1': (None, None),
                '1.3.6.1.4.1.10642.20.17.3.0': (None, None),
            }
            return responses.get(oid, (None, None))

        mock_get.side_effect = side_effect
        result = discover_units.discover_printer('10.0.0.1')

        self.assertIsNotNone(result)
        self.assertEqual(result['unit'], 'unknown')


class TestUpdateConfig(unittest.TestCase):
    """Tests for update_config()."""

    def test_updates_existing_printer(self):
        config = {
            'printers': [
                {'ip': '10.0.0.1', 'model': 'Zebra ZT230'},
                {'ip': '10.0.0.2', 'model': 'Zebra ZT411'},
            ]
        }
        result = discover_units.update_config(config, '10.0.0.1', 'cm')
        self.assertTrue(result)
        self.assertEqual(config['printers'][0]['meter_unit'], 'cm')

    def test_printer_not_found(self):
        config = {'printers': [{'ip': '10.0.0.1'}]}
        result = discover_units.update_config(config, '10.0.0.99', 'cm')
        self.assertFalse(result)

    def test_multiple_printers(self):
        config = {
            'printers': [
                {'ip': '10.0.0.1'},
                {'ip': '10.0.0.2'},
                {'ip': '10.0.0.3'},
            ]
        }
        discover_units.update_config(config, '10.0.0.2', 'mm')
        self.assertEqual(config['printers'][1]['meter_unit'], 'mm')
        self.assertIsNone(config['printers'][0].get('meter_unit'))


class TestDiscoverAll(unittest.TestCase):
    """Tests for discover_all()."""

    @patch('discover_units.discover_printer')
    def test_discovers_all_printers(self, mock_discover):
        config = {
            'printers': [
                {'ip': '10.0.0.1'},
                {'ip': '10.0.0.2'},
            ]
        }
        mock_discover.return_value = {
            'ip': '10.0.0.1',
            'model_name': 'Zebra',
            'serial': 'SN',
            'unit': 'cm',
            'unit_code': 5,
            'meters_available': True,
        }
        results = discover_units.discover_all(config)
        self.assertEqual(len(results), 2)

    @patch('discover_units.discover_printer')
    def test_handles_failures(self, mock_discover):
        config = {
            'printers': [
                {'ip': '10.0.0.1'},
                {'ip': '10.0.0.2'},
            ]
        }
        mock_discover.side_effect = [None, {'ip': '10.0.0.2', 'unit': 'cm'}]
        results = discover_units.discover_all(config)
        self.assertEqual(len(results), 1)


class TestUnitMap(unittest.TestCase):
    """Tests for UNIT_MAP."""

    def test_all_values(self):
        self.assertEqual(discover_units.UNIT_MAP[0], 'other')
        self.assertEqual(discover_units.UNIT_MAP[1], 'tenThousandthsOfSheets')
        self.assertEqual(discover_units.UNIT_MAP[2], 'impressions')
        self.assertEqual(discover_units.UNIT_MAP[3], 'sheets')
        self.assertEqual(discover_units.UNIT_MAP[4], 'linearFeet')
        self.assertEqual(discover_units.UNIT_MAP[5], 'linearMeters')


class TestMainFunction(unittest.TestCase):
    """Tests for main() argument parsing."""

    def test_no_args_shows_help(self):
        with self.assertRaises(SystemExit) as ctx:
            with patch('sys.argv', ['discover_units.py']):
                discover_units.main()
        self.assertEqual(ctx.exception.code, 1)

    def test_save_flag(self):
        with patch('discover_units.load_config') as mock_config:
            mock_config.return_value = {'printers': [{'ip': '10.0.0.1'}]}
            with patch('discover_units.discover_printer') as mock_discover:
                mock_discover.return_value = {'ip': '10.0.0.1', 'unit': 'cm'}
                with patch('discover_units.save_config') as mock_save:
                    with patch('sys.argv', ['discover_units.py', '10.0.0.1', '--save']):
                        discover_units.main()
                    mock_save.assert_called_once()


if __name__ == '__main__':
    unittest.main()
