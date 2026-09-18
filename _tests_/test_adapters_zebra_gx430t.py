"""Tests for adapters/zebra_gx430t.py - Zebra GX430t adapter.

The GX430t adapter is poke-only (no label/meter counters).
Tests cover:
- Poke (reachability via model name OID)
- Forces SNMPv1 (version=0)
- Unreachable printer
- Byte-to-string model name decoding
"""

import sys
import os
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.zebra_gx430t import ZebraGX430tAdapter, OID_REACHABILITY, OID_TOTAL_USAGE
from snmp_client import TAG_OCTET_STRING


class TestZebraGX430tAdapter(unittest.TestCase):
    """Tests for ZebraGX430tAdapter (poke-only, SNMPv1)."""

    def _make_adapter(self, ip='192.168.40.176'):
        return ZebraGX430tAdapter(ip, community='public', timeout_sec=3, retries=0)

    def test_forces_snmpv1(self):
        """GX430t forces SNMPv1 (version=0) regardless of input."""
        adapter = self._make_adapter()
        self.assertEqual(adapter.version, 0)

    def test_oids_empty(self):
        """GX430t has no counter OIDs (poke only)."""
        self.assertEqual(ZebraGX430tAdapter.OIDS, {})

    def test_oid_reachability_defined(self):
        """Module-level OID_REACHABILITY should be defined."""
        self.assertEqual(OID_REACHABILITY, '1.3.6.1.4.1.10642.1.1.0')

    @patch.object(ZebraGX430tAdapter, '_snmp_get')
    def test_poke_only_response(self, mock_get):
        """GX430t responds with model name but has no counters."""
        mock_get.return_value = (b'ZTC GX430t-203dpi ZPL', TAG_OCTET_STRING)
        result = self._make_adapter().get_counters()

        self.assertTrue(result['reachable'])
        self.assertEqual(result['model_name'], 'ZTC GX430t-203dpi ZPL')
        # No counters available
        self.assertIsNone(result['labels_total'])
        self.assertIsNone(result['meters_total'])
        self.assertEqual(result['meter_unit'], 'unknown')

    @patch.object(ZebraGX430tAdapter, '_snmp_get')
    def test_unreachable_printer(self, mock_get):
        """Printer is off the network."""
        mock_get.return_value = (None, None)
        result = self._make_adapter().get_counters()

        self.assertFalse(result['reachable'])
        self.assertIsNone(result['labels_total'])
        self.assertIsNone(result['meters_total'])
        self.assertEqual(result['model_name'], '')

    @patch.object(ZebraGX430tAdapter, '_snmp_get')
    def test_garbage_model_name(self, mock_get):
        """Printer returns non-standard model name with null bytes."""
        mock_get.return_value = (b'GX430t\x00\x01', TAG_OCTET_STRING)
        result = self._make_adapter().get_counters()

        self.assertTrue(result['reachable'])
        self.assertIn('GX430t', result['model_name'])

    @patch.object(ZebraGX430tAdapter, '_snmp_get')
    def test_is_reachable_true(self, mock_get):
        """is_reachable returns True when poke succeeds."""
        mock_get.return_value = (b'ZTC GX430t', TAG_OCTET_STRING)
        adapter = self._make_adapter()
        self.assertTrue(adapter.is_reachable())

    @patch.object(ZebraGX430tAdapter, '_snmp_get')
    def test_is_reachable_false(self, mock_get):
        """is_reachable returns False when poke fails."""
        mock_get.return_value = (None, None)
        adapter = self._make_adapter()
        self.assertFalse(adapter.is_reachable())

    def test_custom_ip(self):
        """Adapter stores custom IP."""
        adapter = ZebraGX430tAdapter('10.0.0.99')
        self.assertEqual(adapter.ip, '10.0.0.99')

    def test_custom_community(self):
        """Adapter stores custom community."""
        adapter = ZebraGX430tAdapter('10.0.0.99', community='private')
        self.assertEqual(adapter.community, 'private')

    @patch.object(ZebraGX430tAdapter, '_snmp_get_retry')
    @patch.object(ZebraGX430tAdapter, '_snmp_get')
    def test_poke_and_usage_called(self, mock_get, mock_retry):
        """get_counters calls _snmp_get (poke) then _snmp_get_retry (usage)."""
        mock_get.return_value = (b'ZTC GX430t', TAG_OCTET_STRING)
        mock_retry.return_value = (b'89667 INCHES, 227775 CENTIMETERS', TAG_OCTET_STRING)
        adapter = self._make_adapter()
        result = adapter.get_counters()

        self.assertTrue(result['reachable'])
        self.assertAlmostEqual(result['meters_total'], 2277.75, places=1)
        self.assertEqual(result['meter_unit'], 'm')
        mock_get.assert_called_with(OID_REACHABILITY, label='poke')
        mock_retry.assert_called_with(OID_TOTAL_USAGE, label='usage')


if __name__ == '__main__':
    unittest.main()
