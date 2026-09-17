"""Tests for adapters/zebra.py - Zebra printer adapter.

Tests cover:
- OID constants
- get_counters() with mocked SNMP
- is_reachable() with mocked SNMP
- Model name decoding
- Serial number decoding
- Status code conversion
- Fallback to standard OIDs
- Unit detection
"""

import sys
import os
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.zebra import (
    ZebraAdapter,
    OID_SYS_DESCR,
    OID_SERIAL,
    OID_HR_MODEL,
    OID_HR_STATUS,
    OID_MARKER_LIFE_COUNT,
    OID_MARKER_COUNTER_UNIT,
    OID_ZEBRA_LABELS_TOTAL,
    OID_ZEBRA_METERS_TOTAL,
    OID_ZEBRA_MODEL_NAME,
    OID_ZEBRA_FRIENDLY_NAME,
    OID_ZEBRA_ALT_LABELS,
    UNIT_MAP,
)
from adapters.base import TAG_INTEGER, TAG_COUNTER32, TAG_OCTET_STRING


class TestOidConstants(unittest.TestCase):
    """Tests for OID constant values."""

    def test_sys_descr(self):
        self.assertEqual(OID_SYS_DESCR, '1.3.6.1.2.1.1.1.0')

    def test_serial(self):
        self.assertEqual(OID_SERIAL, '1.3.6.1.2.1.43.5.1.1.17.1')

    def test_hr_model(self):
        self.assertEqual(OID_HR_MODEL, '1.3.6.1.2.1.25.3.2.1.3.1')

    def test_hr_status(self):
        self.assertEqual(OID_HR_STATUS, '1.3.6.1.2.1.25.3.5.1.1.1')

    def test_marker_life_count(self):
        self.assertEqual(OID_MARKER_LIFE_COUNT, '1.3.6.1.2.1.43.10.2.1.4.1.1')

    def test_marker_counter_unit(self):
        self.assertEqual(OID_MARKER_COUNTER_UNIT, '1.3.6.1.2.1.43.10.2.1.3.1')

    def test_zebra_labels_total(self):
        self.assertEqual(OID_ZEBRA_LABELS_TOTAL, '1.3.6.1.4.1.10642.20.17.2.0')

    def test_zebra_meters_total(self):
        self.assertEqual(OID_ZEBRA_METERS_TOTAL, '1.3.6.1.4.1.10642.20.17.3.0')

    def test_zebra_model_name(self):
        self.assertEqual(OID_ZEBRA_MODEL_NAME, '1.3.6.1.4.1.10642.1.1.0')

    def test_zebra_friendly_name(self):
        self.assertEqual(OID_ZEBRA_FRIENDLY_NAME, '1.3.6.1.4.1.10642.20.3.5.0')

    def test_zebra_alt_labels(self):
        self.assertEqual(OID_ZEBRA_ALT_LABELS, '1.3.6.1.4.1.10642.3.1.6.0')


class TestUnitMap(unittest.TestCase):
    """Tests for UNIT_MAP."""

    def test_unit_map_values(self):
        self.assertEqual(UNIT_MAP[0], 'other')
        self.assertEqual(UNIT_MAP[1], 'tenThousandthsOfSheets')
        self.assertEqual(UNIT_MAP[2], 'impressions')
        self.assertEqual(UNIT_MAP[3], 'sheets')
        self.assertEqual(UNIT_MAP[4], 'linearFeet')
        self.assertEqual(UNIT_MAP[5], 'linearMeters')

    def test_all_keys_present(self):
        expected_keys = {0, 1, 2, 3, 4, 5}
        self.assertEqual(set(UNIT_MAP.keys()), expected_keys)


class TestZebraAdapterInit(unittest.TestCase):
    """Tests for ZebraAdapter initialization."""

    def test_init(self):
        adapter = ZebraAdapter('10.0.0.1')
        self.assertEqual(adapter.ip, '10.0.0.1')
        self.assertEqual(adapter.community, 'public')

    def test_init_with_community(self):
        adapter = ZebraAdapter('10.0.0.1', community='private')
        self.assertEqual(adapter.community, 'private')

    def test_oids_defined(self):
        self.assertIn('labels_total', ZebraAdapter.OIDS)
        self.assertIn('meters_total', ZebraAdapter.OIDS)
        self.assertIn('model_name', ZebraAdapter.OIDS)
        self.assertIn('serial', ZebraAdapter.OIDS)
        self.assertIn('status', ZebraAdapter.OIDS)
        self.assertIn('counter_unit', ZebraAdapter.OIDS)
        self.assertIn('life_count', ZebraAdapter.OIDS)


class TestZebraGetCounters(unittest.TestCase):
    """Tests for ZebraAdapter.get_counters() with mocked SNMP."""

    def _make_adapter(self):
        return ZebraAdapter('10.0.0.1', community='public', timeout_sec=3, retries=0)

    @patch.object(ZebraAdapter, '_snmp_get')
    def test_successful_query(self, mock_get):
        adapter = self._make_adapter()

        def side_effect(oid):
            responses = {
                OID_SYS_DESCR: (b'Zebra ZT230', TAG_OCTET_STRING),
                OID_HR_STATUS: (3, TAG_INTEGER),
                OID_ZEBRA_MODEL_NAME: (b'ZTC ZT230-200dpi ZPL', TAG_OCTET_STRING),
                OID_SERIAL: (b'ABC123', TAG_OCTET_STRING),
                OID_ZEBRA_LABELS_TOTAL: (5000, TAG_COUNTER32),
                OID_ZEBRA_METERS_TOTAL: (1234.5, TAG_COUNTER32),
                OID_MARKER_COUNTER_UNIT: (5, TAG_INTEGER),
            }
            return responses.get(oid, (None, None))

        mock_get.side_effect = side_effect
        result = adapter.get_counters()

        self.assertTrue(result['reachable'])
        self.assertEqual(result['labels_total'], 5000)
        self.assertEqual(result['meters_total'], 1234.5)
        self.assertEqual(result['meter_unit'], 'linearMeters')
        self.assertEqual(result['model_name'], 'ZTC ZT230-200dpi ZPL')
        self.assertEqual(result['serial'], 'ABC123')
        self.assertEqual(result['status'], 'idle')

    @patch.object(ZebraAdapter, '_snmp_get')
    def test_unreachable_printer(self, mock_get):
        adapter = self._make_adapter()
        mock_get.return_value = (None, None)
        result = adapter.get_counters()

        self.assertFalse(result['reachable'])
        self.assertIsNone(result['labels_total'])
        self.assertIsNone(result['meters_total'])

    @patch.object(ZebraAdapter, '_snmp_get')
    def test_labels_fallback_to_life_count(self, mock_get):
        adapter = self._make_adapter()

        def side_effect(oid):
            responses = {
                OID_SYS_DESCR: (b'Zebra', TAG_OCTET_STRING),
                OID_HR_STATUS: (3, TAG_INTEGER),
                OID_ZEBRA_MODEL_NAME: (b'Zebra ZT230', TAG_OCTET_STRING),
                OID_SERIAL: (b'SN123', TAG_OCTET_STRING),
                OID_ZEBRA_LABELS_TOTAL: (None, None),  # vendor OID fails
                OID_ZEBRA_METERS_TOTAL: (100.0, TAG_COUNTER32),
                OID_MARKER_COUNTER_UNIT: (3, TAG_INTEGER),
                OID_MARKER_LIFE_COUNT: (9999, TAG_COUNTER32),  # fallback
            }
            return responses.get(oid, (None, None))

        mock_get.side_effect = side_effect
        result = adapter.get_counters()

        self.assertTrue(result['reachable'])
        self.assertEqual(result['labels_total'], 9999)

    @patch.object(ZebraAdapter, '_snmp_get')
    def test_meters_not_available(self, mock_get):
        adapter = self._make_adapter()

        def side_effect(oid):
            responses = {
                OID_SYS_DESCR: (b'Zebra', TAG_OCTET_STRING),
                OID_HR_STATUS: (3, TAG_INTEGER),
                OID_ZEBRA_MODEL_NAME: (b'Zebra ZT230', TAG_OCTET_STRING),
                OID_SERIAL: (b'SN123', TAG_OCTET_STRING),
                OID_ZEBRA_LABELS_TOTAL: (100, TAG_COUNTER32),
                OID_ZEBRA_METERS_TOTAL: (None, None),  # not available
                OID_MARKER_COUNTER_UNIT: (None, None),
            }
            return responses.get(oid, (None, None))

        mock_get.side_effect = side_effect
        result = adapter.get_counters()

        self.assertIsNone(result['meters_total'])

    @patch.object(ZebraAdapter, '_snmp_get')
    def test_unit_assumed_when_vendor_oid_available(self, mock_get):
        adapter = self._make_adapter()

        def side_effect(oid):
            responses = {
                OID_SYS_DESCR: (b'Zebra', TAG_OCTET_STRING),
                OID_HR_STATUS: (3, TAG_INTEGER),
                OID_ZEBRA_MODEL_NAME: (b'Zebra', TAG_OCTET_STRING),
                OID_SERIAL: (b'SN', TAG_OCTET_STRING),
                OID_ZEBRA_LABELS_TOTAL: (100, TAG_COUNTER32),
                OID_ZEBRA_METERS_TOTAL: (50.0, TAG_COUNTER32),  # available
                OID_MARKER_COUNTER_UNIT: (None, None),  # but unit not
            }
            return responses.get(oid, (None, None))

        mock_get.side_effect = side_effect
        result = adapter.get_counters()

        self.assertIn('assumed', result['meter_unit'])

    @patch.object(ZebraAdapter, '_snmp_get')
    def test_status_codes(self, mock_get):
        adapter = self._make_adapter()

        status_codes = {
            1: 'other',
            2: 'unknown',
            3: 'idle',
            4: 'printing',
            5: 'warmup',
            6: 'stopping',
            7: 'down',
        }

        for code, expected_status in status_codes.items():
            mock_get.side_effect = lambda oid, c=code: {
                OID_SYS_DESCR: (b'Zebra', TAG_OCTET_STRING),
                OID_HR_STATUS: (c, TAG_INTEGER),
                OID_ZEBRA_MODEL_NAME: (b'Zebra', TAG_OCTET_STRING),
                OID_SERIAL: (b'SN', TAG_OCTET_STRING),
                OID_ZEBRA_LABELS_TOTAL: (0, TAG_COUNTER32),
                OID_ZEBRA_METERS_TOTAL: (0, TAG_COUNTER32),
                OID_MARKER_COUNTER_UNIT: (None, None),
            }.get(oid, (None, None))

            result = adapter.get_counters()
            self.assertEqual(result['status'], expected_status, f'Status code {code}')

    @patch.object(ZebraAdapter, '_snmp_get')
    def test_model_name_bytes_decoded(self, mock_get):
        adapter = self._make_adapter()

        def side_effect(oid):
            responses = {
                OID_SYS_DESCR: (b'Zebra', TAG_OCTET_STRING),
                OID_HR_STATUS: (3, TAG_INTEGER),
                OID_ZEBRA_MODEL_NAME: (b'ZTC ZD621-203dpi ZPL', TAG_OCTET_STRING),
                OID_SERIAL: (b'XYZ789', TAG_OCTET_STRING),
                OID_ZEBRA_LABELS_TOTAL: (100, TAG_COUNTER32),
                OID_ZEBRA_METERS_TOTAL: (50.0, TAG_COUNTER32),
                OID_MARKER_COUNTER_UNIT: (None, None),
            }
            return responses.get(oid, (None, None))

        mock_get.side_effect = side_effect
        result = adapter.get_counters()

        self.assertEqual(result['model_name'], 'ZTC ZD621-203dpi ZPL')
        self.assertEqual(result['serial'], 'XYZ789')


class TestZebraIsReachable(unittest.TestCase):
    """Tests for ZebraAdapter.is_reachable()."""

    @patch.object(ZebraAdapter, '_snmp_get')
    def test_reachable(self, mock_get):
        mock_get.return_value = (b'Zebra ZebraNet', TAG_OCTET_STRING)
        adapter = ZebraAdapter('10.0.0.1')
        self.assertTrue(adapter.is_reachable())

    @patch.object(ZebraAdapter, '_snmp_get')
    def test_not_reachable(self, mock_get):
        mock_get.return_value = (None, None)
        adapter = ZebraAdapter('10.0.0.1')
        self.assertFalse(adapter.is_reachable())


class TestZebraAdapterInheritance(unittest.TestCase):
    """Tests for ZebraAdapter inheritance."""

    def test_inherits_printer_adapter(self):
        from adapters.base import PrinterAdapter
        adapter = ZebraAdapter('10.0.0.1')
        self.assertIsInstance(adapter, PrinterAdapter)

    def test_has_get_counters(self):
        adapter = ZebraAdapter('10.0.0.1')
        self.assertTrue(hasattr(adapter, 'get_counters'))
        self.assertTrue(callable(adapter.get_counters))

    def test_has_is_reachable(self):
        adapter = ZebraAdapter('10.0.0.1')
        self.assertTrue(hasattr(adapter, 'is_reachable'))
        self.assertTrue(callable(adapter.is_reachable))

    def test_has_snmp_get(self):
        adapter = ZebraAdapter('10.0.0.1')
        self.assertTrue(hasattr(adapter, '_snmp_get'))

    def test_has_snmp_get_multiple(self):
        adapter = ZebraAdapter('10.0.0.1')
        self.assertTrue(hasattr(adapter, '_snmp_get_multiple'))


if __name__ == '__main__':
    unittest.main()
