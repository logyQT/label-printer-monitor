"""Tests for adapters/base.py - abstract printer adapter interface.

Tests cover:
- Abstract class enforcement
- Status code conversion
- Helper methods
"""

import sys
import os
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.base import PrinterAdapter, TAG_COUNTER32, TAG_GAUGE32, TAG_INTEGER


class ConcretePrinterAdapter(PrinterAdapter):
    """Concrete implementation for testing abstract class."""

    def get_counters(self):
        return {
            'labels_total': 100,
            'meters_total': 50.5,
            'meter_unit': 'cm',
            'model_name': 'Test Printer',
            'serial': 'ABC123',
            'status': 'idle',
            'reachable': True,
        }

    def is_reachable(self):
        return True


class FailingPrinterAdapter(PrinterAdapter):
    """Adapter that returns unreachable."""

    def get_counters(self):
        return {
            'labels_total': None,
            'meters_total': None,
            'meter_unit': 'unknown',
            'model_name': '',
            'serial': '',
            'status': 'offline',
            'reachable': False,
        }

    def is_reachable(self):
        return False


class TestPrinterAdapterAbstract(unittest.TestCase):
    """Tests for abstract class behavior."""

    def test_cannot_instantiate_directly(self):
        with self.assertRaises(TypeError):
            PrinterAdapter('10.0.0.1')

    def test_concrete_instantiation(self):
        adapter = ConcretePrinterAdapter('10.0.0.1')
        self.assertEqual(adapter.ip, '10.0.0.1')

    def test_default_community(self):
        adapter = ConcretePrinterAdapter('10.0.0.1')
        self.assertEqual(adapter.community, 'public')

    def test_custom_community(self):
        adapter = ConcretePrinterAdapter('10.0.0.1', community='private')
        self.assertEqual(adapter.community, 'private')

    def test_custom_timeout(self):
        adapter = ConcretePrinterAdapter('10.0.0.1', timeout_sec=5)
        self.assertEqual(adapter.timeout_sec, 5)

    def test_custom_retries(self):
        adapter = ConcretePrinterAdapter('10.0.0.1', retries=3)
        self.assertEqual(adapter.retries, 3)


class TestStatusFromCode(unittest.TestCase):
    """Tests for _status_from_code()."""

    def test_none_returns_offline(self):
        result = PrinterAdapter._status_from_code(None)
        self.assertEqual(result, 'offline')

    def test_idle(self):
        result = PrinterAdapter._status_from_code(3)
        self.assertEqual(result, 'idle')

    def test_printing(self):
        result = PrinterAdapter._status_from_code(4)
        self.assertEqual(result, 'printing')

    def test_warmup(self):
        result = PrinterAdapter._status_from_code(5)
        self.assertEqual(result, 'warmup')

    def test_other(self):
        result = PrinterAdapter._status_from_code(1)
        self.assertEqual(result, 'other')

    def test_unknown(self):
        result = PrinterAdapter._status_from_code(99)
        self.assertEqual(result, 'unknown')

    def test_all_codes(self):
        expected = {
            1: 'other',
            2: 'unknown',
            3: 'idle',
            4: 'printing',
            5: 'warmup',
            6: 'stopping',
            7: 'down',
        }
        for code, status in expected.items():
            result = PrinterAdapter._status_from_code(code)
            self.assertEqual(result, status, f'Code {code}')


class TestGetCounters(unittest.TestCase):
    """Tests for get_counters() implementations."""

    def test_concrete_adapter_returns_dict(self):
        adapter = ConcretePrinterAdapter('10.0.0.1')
        result = adapter.get_counters()
        self.assertIsInstance(result, dict)
        self.assertTrue(result['reachable'])

    def test_concrete_adapter_values(self):
        adapter = ConcretePrinterAdapter('10.0.0.1')
        result = adapter.get_counters()
        self.assertEqual(result['labels_total'], 100)
        self.assertEqual(result['meters_total'], 50.5)
        self.assertEqual(result['meter_unit'], 'cm')
        self.assertEqual(result['model_name'], 'Test Printer')
        self.assertEqual(result['serial'], 'ABC123')
        self.assertEqual(result['status'], 'idle')

    def test_failing_adapter_returns_unreachable(self):
        adapter = FailingPrinterAdapter('10.0.0.1')
        result = adapter.get_counters()
        self.assertFalse(result['reachable'])
        self.assertIsNone(result['labels_total'])
        self.assertIsNone(result['meters_total'])


class TestIsReachable(unittest.TestCase):
    """Tests for is_reachable() implementations."""

    def test_concrete_reachable(self):
        adapter = ConcretePrinterAdapter('10.0.0.1')
        self.assertTrue(adapter.is_reachable())

    def test_failing_not_reachable(self):
        adapter = FailingPrinterAdapter('10.0.0.1')
        self.assertFalse(adapter.is_reachable())


class TestSnmpGetHelper(unittest.TestCase):
    """Tests for _snmp_get() helper."""

    @patch('snmp_client.get')
    def test_successful_get(self, mock_get):
        mock_get.return_value = (42, TAG_INTEGER)
        adapter = ConcretePrinterAdapter('10.0.0.1')
        value, tag = adapter._snmp_get('1.3.6.1.2.1.1.1.0')
        self.assertEqual(value, 42)
        self.assertEqual(tag, TAG_INTEGER)
        mock_get.assert_called_once_with(
            '10.0.0.1', '1.3.6.1.2.1.1.1.0',
            community='public', timeout_sec=5, retries=2, version=0,
        )

    @patch('snmp_client.get')
    def test_timeout_returns_none(self, mock_get):
        from snmp_client import SnmpTimeout
        mock_get.side_effect = SnmpTimeout('timeout')
        adapter = ConcretePrinterAdapter('10.0.0.1')
        value, tag = adapter._snmp_get('1.3.6.1.2.1.1.1.0')
        self.assertIsNone(value)
        self.assertIsNone(tag)

    @patch('snmp_client.get')
    def test_snmp_error_returns_none(self, mock_get):
        from snmp_client import SnmpError
        mock_get.side_effect = SnmpError('error')
        adapter = ConcretePrinterAdapter('10.0.0.1')
        value, tag = adapter._snmp_get('1.3.6.1.2.1.1.1.0')
        self.assertIsNone(value)
        self.assertIsNone(tag)


class TestSnmpGetMultipleHelper(unittest.TestCase):
    """Tests for _snmp_get_multiple() helper."""

    @patch('snmp_client.get_multiple')
    def test_successful_get(self, mock_get):
        mock_get.return_value = [
            ('1.3.6.1.2.1.1.1.0', 42, TAG_INTEGER),
            ('1.3.6.1.2.1.1.5.0', b'test', 0x04),
        ]
        adapter = ConcretePrinterAdapter('10.0.0.1')
        results = adapter._snmp_get_multiple(['1.3.6.1.2.1.1.1.0', '1.3.6.1.2.1.1.5.0'])
        self.assertEqual(len(results), 2)

    @patch('snmp_client.get_multiple')
    def test_timeout_returns_none_list(self, mock_get):
        from snmp_client import SnmpTimeout
        mock_get.side_effect = SnmpTimeout('timeout')
        adapter = ConcretePrinterAdapter('10.0.0.1')
        results = adapter._snmp_get_multiple(['1.3.6.1.2.1.1.1.0'])
        self.assertEqual(len(results), 1)
        self.assertIsNone(results[0][1])


class TestOidConstants(unittest.TestCase):
    """Tests for OID constants in base module."""

    def test_tag_values(self):
        self.assertEqual(TAG_COUNTER32, 0x41)
        self.assertEqual(TAG_GAUGE32, 0x42)
        self.assertEqual(TAG_INTEGER, 0x02)


if __name__ == '__main__':
    unittest.main()
