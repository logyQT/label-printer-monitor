"""Tests for adapters/zebra.py - Zebra printer adapter."""

import sys
import os
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.zebra import ZebraAdapter, OID_REACHABILITY, OID_LABELS, OID_METERS


class TestZebraAdapterInit(unittest.TestCase):
    def test_init(self):
        adapter = ZebraAdapter('10.0.0.1')
        self.assertEqual(adapter.ip, '10.0.0.1')
        self.assertEqual(adapter.community, 'public')

    def test_oids_defined(self):
        self.assertIn('labels_total', ZebraAdapter.OIDS)


class TestZebraGetCounters(unittest.TestCase):
    def _make_adapter(self):
        return ZebraAdapter('10.0.0.1', community='public', timeout_sec=3, retries=0)

    @patch.object(ZebraAdapter, '_snmp_get_retry')
    @patch.object(ZebraAdapter, '_snmp_get')
    def test_successful_query(self, mock_get, mock_retry):
        mock_get.return_value = (b'ZTC ZT411-300dpi ZPL', 0x04)
        mock_retry.side_effect = [
            (302318, 0x02),  # labels
            (1411527, 0x02),  # meters
        ]
        result = self._make_adapter().get_counters()

        self.assertTrue(result['reachable'])
        self.assertEqual(result['labels_total'], 302318)
        self.assertEqual(result['meters_total'], 1411527.0)
        self.assertEqual(result['meter_unit'], 'cm')
        self.assertEqual(result['model_name'], 'ZTC ZT411-300dpi ZPL')

    @patch.object(ZebraAdapter, '_snmp_get')
    def test_unreachable(self, mock_get):
        mock_get.return_value = (None, None)
        result = self._make_adapter().get_counters()
        self.assertFalse(result['reachable'])
        self.assertIsNone(result['labels_total'])
        self.assertIsNone(result['meters_total'])

    @patch.object(ZebraAdapter, '_snmp_get_retry')
    @patch.object(ZebraAdapter, '_snmp_get')
    def test_meters_not_available(self, mock_get, mock_retry):
        mock_get.return_value = (b'Zebra', 0x04)
        mock_retry.side_effect = [
            (100, 0x02),  # labels OK
            (None, None),  # meters fails
        ]
        result = self._make_adapter().get_counters()
        self.assertIsNone(result['meters_total'])
        self.assertEqual(result['meter_unit'], 'unknown')


class TestZebraIsReachable(unittest.TestCase):
    @patch.object(ZebraAdapter, '_snmp_get')
    def test_reachable(self, mock_get):
        mock_get.return_value = (b'Zebra', 0x04)
        self.assertTrue(ZebraAdapter('10.0.0.1').is_reachable())

    @patch.object(ZebraAdapter, '_snmp_get')
    def test_not_reachable(self, mock_get):
        mock_get.return_value = (None, None)
        self.assertFalse(ZebraAdapter('10.0.0.1').is_reachable())


if __name__ == '__main__':
    unittest.main()
