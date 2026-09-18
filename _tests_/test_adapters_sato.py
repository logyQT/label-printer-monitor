"""Tests for adapters/sato.py - Sato printer adapter.

Tests cover:
- OID constants
- get_counters() with mocked SNMP
- is_reachable() with mocked SNMP
- Unit code mapping
- Unmapped unit codes
- Model name decoding
"""

import sys
import os
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.sato import (
    SatoAdapter,
    OID_PRINTER_NAME,
    OID_SERIAL,
    OID_MARKER_LIFE_COUNT,
    OID_MARKER_COUNTER_UNIT,
)
from adapters.base import TAG_INTEGER, TAG_COUNTER32, TAG_OCTET_STRING


class TestOidConstants(unittest.TestCase):
    """Tests for OID constant values."""

    def test_printer_name(self):
        self.assertEqual(OID_PRINTER_NAME, '1.3.6.1.2.1.43.5.1.1.16.1')

    def test_serial(self):
        self.assertEqual(OID_SERIAL, '1.3.6.1.2.1.43.5.1.1.17.1')

    def test_marker_life_count(self):
        self.assertEqual(OID_MARKER_LIFE_COUNT, '1.3.6.1.2.1.43.10.2.1.4.1.1')

    def test_marker_counter_unit(self):
        self.assertEqual(OID_MARKER_COUNTER_UNIT, '1.3.6.1.2.1.43.10.2.1.3.1.1')


class TestSatoAdapterInit(unittest.TestCase):
    """Tests for SatoAdapter initialization."""

    def test_init(self):
        adapter = SatoAdapter('10.0.0.1')
        self.assertEqual(adapter.ip, '10.0.0.1')
        self.assertEqual(adapter.community, 'public')
        self.assertEqual(adapter.unit_map, {})

    def test_init_with_unit_map(self):
        adapter = SatoAdapter('10.0.0.1', unit_map={'17': 'm'})
        self.assertEqual(adapter.unit_map, {'17': 'm'})

    def test_oids_defined(self):
        self.assertIn('model_name', SatoAdapter.OIDS)
        self.assertIn('meters_total', SatoAdapter.OIDS)
        self.assertIn('counter_unit', SatoAdapter.OIDS)


class TestSatoGetCounters(unittest.TestCase):
    """Tests for SatoAdapter.get_counters() with mocked SNMP."""

    def _make_adapter(self, unit_map=None):
        return SatoAdapter('10.0.0.1', community='public', timeout_sec=3,
                           retries=0, unit_map=unit_map or {})

    @patch.object(SatoAdapter, '_snmp_get_multiple')
    @patch.object(SatoAdapter, '_snmp_get')
    def test_successful_query(self, mock_get, mock_get_multi):
        adapter = self._make_adapter(unit_map={'17': 'm'})

        def side_effect(oid, label=None):
            responses = {
                OID_PRINTER_NAME: (b'CL4NX Plus 305dpi', TAG_OCTET_STRING),
            }
            return responses.get(oid, (None, None))

        def multi_side_effect(oids, label=None):
            responses = {
                OID_MARKER_LIFE_COUNT: (27410, TAG_COUNTER32),
                OID_MARKER_COUNTER_UNIT: (17, TAG_INTEGER),
            }
            return [(oid, *responses.get(oid, (None, None))) for oid in oids]

        mock_get.side_effect = side_effect
        mock_get_multi.side_effect = multi_side_effect
        result = adapter.get_counters()

        self.assertTrue(result['reachable'])
        self.assertEqual(result['meters_total'], 27410.0)
        self.assertEqual(result['labels_total'], None)
        self.assertEqual(result['meter_unit'], 'm')
        self.assertEqual(result['model_name'], 'CL4NX Plus 305dpi')

    @patch.object(SatoAdapter, '_snmp_get')
    def test_unreachable_printer(self, mock_get):
        adapter = self._make_adapter()
        mock_get.return_value = (None, None)
        result = adapter.get_counters()

        self.assertFalse(result['reachable'])
        self.assertIsNone(result['meters_total'])
        self.assertEqual(result['labels_total'], None)

    @patch.object(SatoAdapter, '_snmp_get_multiple')
    @patch.object(SatoAdapter, '_snmp_get')
    def test_unmapped_unit_code(self, mock_get, mock_get_multi):
        adapter = self._make_adapter(unit_map={})

        def side_effect(oid, label=None):
            responses = {
                OID_PRINTER_NAME: (b'CL4NX Plus 305dpi', TAG_OCTET_STRING),
            }
            return responses.get(oid, (None, None))

        def multi_side_effect(oids, label=None):
            responses = {
                OID_MARKER_LIFE_COUNT: (1000, TAG_COUNTER32),
                OID_MARKER_COUNTER_UNIT: (99, TAG_INTEGER),
            }
            return [(oid, *responses.get(oid, (None, None))) for oid in oids]

        mock_get.side_effect = side_effect
        mock_get_multi.side_effect = multi_side_effect
        result = adapter.get_counters()

        self.assertTrue(result['reachable'])
        self.assertEqual(result['meter_unit'], 'unit_code:99')

    @patch.object(SatoAdapter, '_snmp_get_multiple')
    @patch.object(SatoAdapter, '_snmp_get')
    def test_no_unit_oid(self, mock_get, mock_get_multi):
        adapter = self._make_adapter()

        def side_effect(oid, label=None):
            responses = {
                OID_PRINTER_NAME: (b'CL4NX Plus 305dpi', TAG_OCTET_STRING),
            }
            return responses.get(oid, (None, None))

        def multi_side_effect(oids, label=None):
            responses = {
                OID_MARKER_LIFE_COUNT: (5000, TAG_COUNTER32),
                OID_MARKER_COUNTER_UNIT: (None, None),
            }
            return [(oid, *responses.get(oid, (None, None))) for oid in oids]

        mock_get.side_effect = side_effect
        mock_get_multi.side_effect = multi_side_effect
        result = adapter.get_counters()

        self.assertTrue(result['reachable'])
        self.assertEqual(result['meters_total'], 5000.0)
        self.assertEqual(result['meter_unit'], 'unknown')

    @patch.object(SatoAdapter, '_snmp_get_multiple')
    @patch.object(SatoAdapter, '_snmp_get')
    def test_meters_not_available(self, mock_get, mock_get_multi):
        adapter = self._make_adapter()

        def side_effect(oid, label=None):
            responses = {
                OID_PRINTER_NAME: (b'CL4NX Plus 305dpi', TAG_OCTET_STRING),
            }
            return responses.get(oid, (None, None))

        def multi_side_effect(oids, label=None):
            responses = {
                OID_MARKER_LIFE_COUNT: (None, None),
                OID_MARKER_COUNTER_UNIT: (None, None),
            }
            return [(oid, *responses.get(oid, (None, None))) for oid in oids]

        mock_get.side_effect = side_effect
        mock_get_multi.side_effect = multi_side_effect
        result = adapter.get_counters()

        self.assertTrue(result['reachable'])
        self.assertIsNone(result['meters_total'])

    @patch.object(SatoAdapter, '_snmp_get_multiple')
    @patch.object(SatoAdapter, '_snmp_get')
    def test_model_name_bytes_decoded(self, mock_get, mock_get_multi):
        adapter = self._make_adapter()

        def side_effect(oid, label=None):
            responses = {
                OID_PRINTER_NAME: (b'SATO CL4NX Plus 305dpi', TAG_OCTET_STRING),
            }
            return responses.get(oid, (None, None))

        def multi_side_effect(oids, label=None):
            responses = {
                OID_MARKER_LIFE_COUNT: (100, TAG_COUNTER32),
                OID_MARKER_COUNTER_UNIT: (17, TAG_INTEGER),
            }
            return [(oid, *responses.get(oid, (None, None))) for oid in oids]

        mock_get.side_effect = side_effect
        mock_get_multi.side_effect = multi_side_effect
        result = adapter.get_counters()

        self.assertEqual(result['model_name'], 'SATO CL4NX Plus 305dpi')

    @patch.object(SatoAdapter, '_snmp_get_multiple')
    @patch.object(SatoAdapter, '_snmp_get')
    def test_labels_always_none(self, mock_get, mock_get_multi):
        adapter = self._make_adapter(unit_map={'17': 'm'})

        def side_effect(oid, label=None):
            responses = {
                OID_PRINTER_NAME: (b'CL4NX Plus', TAG_OCTET_STRING),
            }
            return responses.get(oid, (None, None))

        def multi_side_effect(oids, label=None):
            responses = {
                OID_MARKER_LIFE_COUNT: (1000, TAG_COUNTER32),
                OID_MARKER_COUNTER_UNIT: (17, TAG_INTEGER),
            }
            return [(oid, *responses.get(oid, (None, None))) for oid in oids]

        mock_get.side_effect = side_effect
        mock_get_multi.side_effect = multi_side_effect
        result = adapter.get_counters()

        self.assertIsNone(result['labels_total'])


class TestSatoIsReachable(unittest.TestCase):
    """Tests for SatoAdapter.is_reachable()."""

    @patch.object(SatoAdapter, '_snmp_get')
    def test_reachable(self, mock_get):
        mock_get.return_value = (b'CL4NX Plus', TAG_OCTET_STRING)
        adapter = SatoAdapter('10.0.0.1')
        self.assertTrue(adapter.is_reachable())

    @patch.object(SatoAdapter, '_snmp_get')
    def test_not_reachable(self, mock_get):
        mock_get.return_value = (None, None)
        adapter = SatoAdapter('10.0.0.1')
        self.assertFalse(adapter.is_reachable())


class TestSatoAdapterInheritance(unittest.TestCase):
    """Tests for SatoAdapter inheritance."""

    def test_inherits_printer_adapter(self):
        from adapters.base import PrinterAdapter
        adapter = SatoAdapter('10.0.0.1')
        self.assertIsInstance(adapter, PrinterAdapter)

    def test_has_get_counters(self):
        adapter = SatoAdapter('10.0.0.1')
        self.assertTrue(hasattr(adapter, 'get_counters'))
        self.assertTrue(callable(adapter.get_counters))

    def test_has_is_reachable(self):
        adapter = SatoAdapter('10.0.0.1')
        self.assertTrue(hasattr(adapter, 'is_reachable'))
        self.assertTrue(callable(adapter.is_reachable))


if __name__ == '__main__':
    unittest.main()
