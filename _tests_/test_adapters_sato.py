"""Tests for adapters/sato.py - Sato printer adapter."""

import sys
import os
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.sato import SatoAdapter, OID_REACHABILITY, OID_METERS, OID_UNIT


class TestSatoAdapterInit(unittest.TestCase):
    def test_init(self):
        adapter = SatoAdapter('10.0.0.1')
        self.assertEqual(adapter.ip, '10.0.0.1')
        self.assertEqual(adapter.unit_map, {})

    def test_init_with_unit_map(self):
        adapter = SatoAdapter('10.0.0.1', unit_map={'17': 'm'})
        self.assertEqual(adapter.unit_map, {'17': 'm'})

    def test_oids_defined(self):
        self.assertIn('model_name', SatoAdapter.OIDS)
        self.assertIn('meters_total', SatoAdapter.OIDS)


class TestSatoGetCounters(unittest.TestCase):
    def _make_adapter(self, unit_map=None):
        return SatoAdapter('10.0.0.1', community='public', timeout_sec=3,
                           retries=0, unit_map=unit_map or {})

    @patch.object(SatoAdapter, '_snmp_get_retry')
    @patch.object(SatoAdapter, '_snmp_get')
    def test_successful_query(self, mock_get, mock_retry):
        mock_get.return_value = (b'CL4NX Plus 305dpi', 0x04)
        mock_retry.side_effect = [
            (27410, 0x41),  # meters
            (17, 0x02),     # unit
        ]
        result = self._make_adapter(unit_map={'17': 'm'}).get_counters()

        self.assertTrue(result['reachable'])
        self.assertEqual(result['meters_total'], 27410.0)
        self.assertEqual(result['meter_unit'], 'm')
        self.assertEqual(result['model_name'], 'CL4NX Plus 305dpi')

    @patch.object(SatoAdapter, '_snmp_get')
    def test_unreachable(self, mock_get):
        mock_get.return_value = (None, None)
        result = self._make_adapter().get_counters()
        self.assertFalse(result['reachable'])
        self.assertIsNone(result['meters_total'])

    @patch.object(SatoAdapter, '_snmp_get_retry')
    @patch.object(SatoAdapter, '_snmp_get')
    def test_unmapped_unit_code(self, mock_get, mock_retry):
        mock_get.return_value = (b'CL4NX Plus', 0x04)
        mock_retry.side_effect = [
            (1000, 0x41),  # meters
            (99, 0x02),    # unit (unmapped)
        ]
        result = self._make_adapter(unit_map={}).get_counters()
        self.assertEqual(result['meter_unit'], 'unit_code:99')

    @patch.object(SatoAdapter, '_snmp_get_retry')
    @patch.object(SatoAdapter, '_snmp_get')
    def test_meters_not_available(self, mock_get, mock_retry):
        mock_get.return_value = (b'CL4NX Plus', 0x04)
        mock_retry.side_effect = [
            (None, None),  # meters fails
            (None, None),  # unit fails
        ]
        result = self._make_adapter().get_counters()
        self.assertIsNone(result['meters_total'])
        self.assertEqual(result['meter_unit'], 'unknown')

    @patch.object(SatoAdapter, '_snmp_get_retry')
    @patch.object(SatoAdapter, '_snmp_get')
    def test_labels_always_none(self, mock_get, mock_retry):
        mock_get.return_value = (b'CL4NX Plus', 0x04)
        mock_retry.side_effect = [
            (1000, 0x41),  # meters
            (17, 0x02),    # unit
        ]
        result = self._make_adapter(unit_map={'17': 'm'}).get_counters()
        self.assertIsNone(result['labels_total'])


class TestSatoIsReachable(unittest.TestCase):
    @patch.object(SatoAdapter, '_snmp_get')
    def test_reachable(self, mock_get):
        mock_get.return_value = (b'CL4NX Plus', 0x04)
        self.assertTrue(SatoAdapter('10.0.0.1').is_reachable())

    @patch.object(SatoAdapter, '_snmp_get')
    def test_not_reachable(self, mock_get):
        mock_get.return_value = (None, None)
        self.assertFalse(SatoAdapter('10.0.0.1').is_reachable())


if __name__ == '__main__':
    unittest.main()
