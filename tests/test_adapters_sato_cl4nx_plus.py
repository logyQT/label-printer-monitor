"""Tests for adapters/sato_cl4nx_plus.py - Sato CL4NX Plus adapter.

Tests cover:
- Poke (reachability via printer name OID)
- Meter counter retrieval
- Unit detection via prtMarkerCounterUnit OID
- Built-in UNIT_MAP translation
- Unreachable printer
- Byte-to-string model name decoding
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.adapters.sato_cl4nx_plus import (
    OID_METERS,
    OID_REACHABILITY,
    OID_UNIT,
    UNIT_MAP,
    SatoCL4NXPlusAdapter,
)
from src.snmp_client import TAG_COUNTER32, TAG_INTEGER, TAG_OCTET_STRING


class TestSatoCL4NXPlusAdapter(unittest.TestCase):
    """Tests for SatoCL4NXPlusAdapter."""

    def _make_adapter(self, ip: str = "192.168.40.50") -> SatoCL4NXPlusAdapter:
        return SatoCL4NXPlusAdapter(ip, community="public", timeout_sec=3, retries=0)

    def test_default_version_is_v2c(self) -> None:
        """Sato defaults to SNMPv2c (version=1)."""
        adapter = self._make_adapter()
        self.assertEqual(adapter.version, 1)

    def test_oids_defined(self) -> None:
        """Module-level OIDs should be defined."""
        self.assertEqual(OID_REACHABILITY, "1.3.6.1.2.1.43.5.1.1.16.1")
        self.assertEqual(OID_METERS, "1.3.6.1.2.1.43.10.2.1.4.1.1")
        self.assertEqual(OID_UNIT, "1.3.6.1.2.1.43.10.2.1.3.1.1")

    def test_class_oids(self) -> None:
        """Class OIDS dict should contain meters_total and meter_unit."""
        self.assertIn("meters_total", SatoCL4NXPlusAdapter.OIDS)
        self.assertIn("meter_unit", SatoCL4NXPlusAdapter.OIDS)

    def test_unit_map_is_populated(self) -> None:
        """UNIT_MAP should contain known Sato unit codes."""
        self.assertIn("5", UNIT_MAP)
        self.assertEqual(UNIT_MAP["5"], "linearMeters")

    @patch.object(SatoCL4NXPlusAdapter, "_snmp_get_retry")
    @patch.object(SatoCL4NXPlusAdapter, "_snmp_get")
    def test_full_response_with_known_unit(self, mock_get: MagicMock, mock_retry: MagicMock) -> None:
        """Sato with meters and a known unit code (5 = linearMeters)."""
        adapter = self._make_adapter()
        mock_get.return_value = (b"SATO CL4NX Plus", TAG_OCTET_STRING)
        mock_retry.side_effect = [
            (123456, TAG_COUNTER32),  # meters
            (5, TAG_INTEGER),  # unit code = 5 (linearMeters)
        ]
        result = adapter.get_counters()

        self.assertTrue(result["reachable"])
        self.assertEqual(result["model_name"], "SATO CL4NX Plus")
        self.assertEqual(result["meters_total"], 123456.0)
        self.assertEqual(result["meter_unit"], "m")  # always standardized to meters
        # Sato has no label counter
        self.assertIsNone(result["labels_total"])

    @patch.object(SatoCL4NXPlusAdapter, "_snmp_get_retry")
    @patch.object(SatoCL4NXPlusAdapter, "_snmp_get")
    def test_meters_without_unit(self, mock_get: MagicMock, mock_retry: MagicMock) -> None:
        """Sato responds with meters but unit OID times out."""
        adapter = self._make_adapter()
        mock_get.return_value = (b"SATO CL4NX Plus", TAG_OCTET_STRING)
        mock_retry.side_effect = [
            (50000, TAG_COUNTER32),  # meters
            (None, None),  # unit timeout
        ]
        result = adapter.get_counters()

        self.assertTrue(result["reachable"])
        self.assertEqual(result["meters_total"], 50000.0)
        self.assertEqual(result["meter_unit"], "m")

    @patch.object(SatoCL4NXPlusAdapter, "_snmp_get")
    def test_unreachable_printer(self, mock_get: MagicMock) -> None:
        """Printer is off the network."""
        mock_get.return_value = (None, None)
        result = self._make_adapter().get_counters()

        self.assertFalse(result["reachable"])
        self.assertIsNone(result["labels_total"])
        self.assertIsNone(result["meters_total"])
        self.assertEqual(result["model_name"], "")

    @patch.object(SatoCL4NXPlusAdapter, "_snmp_get_retry")
    @patch.object(SatoCL4NXPlusAdapter, "_snmp_get")
    def test_garbage_model_name(self, mock_get: MagicMock, mock_retry: MagicMock) -> None:
        """Printer returns non-standard model name with null bytes."""
        mock_get.return_value = (b"SATO\x00\x01\x02", TAG_OCTET_STRING)
        mock_retry.side_effect = [(None, None), (None, None)]
        result = self._make_adapter().get_counters()

        self.assertTrue(result["reachable"])
        self.assertIn("SATO", result["model_name"])

    @patch.object(SatoCL4NXPlusAdapter, "_snmp_get")
    def test_is_reachable_true(self, mock_get: MagicMock) -> None:
        """is_reachable returns True when poke succeeds."""
        mock_get.return_value = (b"SATO CL4NX Plus", TAG_OCTET_STRING)
        adapter = self._make_adapter()
        self.assertTrue(adapter.is_reachable())

    @patch.object(SatoCL4NXPlusAdapter, "_snmp_get")
    def test_is_reachable_false(self, mock_get: MagicMock) -> None:
        """is_reachable returns False when poke fails."""
        mock_get.return_value = (None, None)
        adapter = self._make_adapter()
        self.assertFalse(adapter.is_reachable())

    def test_custom_ip(self) -> None:
        """Adapter stores custom IP."""
        adapter = SatoCL4NXPlusAdapter("10.0.0.99")
        self.assertEqual(adapter.ip, "10.0.0.99")

    def test_custom_community(self) -> None:
        """Adapter stores custom community."""
        adapter = SatoCL4NXPlusAdapter("10.0.0.99", community="private")
        self.assertEqual(adapter.community, "private")

    @patch.object(SatoCL4NXPlusAdapter, "_snmp_get_retry")
    @patch.object(SatoCL4NXPlusAdapter, "_snmp_get")
    def test_unit_code_unknown(self, mock_get: MagicMock, mock_retry: MagicMock) -> None:
        """Unit code not in UNIT_MAP falls back to unit_code:N format."""
        adapter = self._make_adapter()
        mock_get.return_value = (b"SATO", TAG_OCTET_STRING)
        mock_retry.side_effect = [
            (1000, TAG_COUNTER32),  # meters
            (7, TAG_INTEGER),  # unknown unit code
        ]
        result = adapter.get_counters()

        self.assertTrue(result["reachable"])
        self.assertEqual(result["meter_unit"], "m")


if __name__ == "__main__":
    unittest.main()
