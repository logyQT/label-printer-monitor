"""Tests for adapters/zebra_zt411.py - Zebra ZT411 adapter.

Tests cover:
- Poke (reachability via model name OID)
- Label counter retrieval
- Meter counter retrieval (cm)
- Unreachable printer
- Partial OID responses
- Byte-to-string model name decoding
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.adapters.zebra_zt411 import OID_LABELS, OID_METERS, OID_REACHABILITY, ZebraZT411Adapter
from src.snmp_client import TAG_COUNTER32, TAG_OCTET_STRING


class TestZebraZT411Adapter(unittest.TestCase):
    """Tests for ZebraZT411Adapter."""

    def _make_adapter(self, ip: str = "192.168.40.100") -> ZebraZT411Adapter:
        return ZebraZT411Adapter(ip, community="public", timeout_sec=3, retries=0)

    def test_default_version_is_v2c(self) -> None:
        """ZT411 defaults to SNMPv2c (version=1)."""
        adapter = self._make_adapter()
        self.assertEqual(adapter.version, 1)

    def test_oids_defined(self) -> None:
        """Module-level OIDs should be defined."""
        self.assertEqual(OID_REACHABILITY, "1.3.6.1.4.1.10642.1.1.0")
        self.assertEqual(OID_LABELS, "1.3.6.1.4.1.10642.3.1.6.0")
        self.assertEqual(OID_METERS, "1.3.6.1.4.1.10642.3.1.1.0")

    def test_class_oids(self) -> None:
        """Class OIDS dict should contain labels_total."""
        self.assertIn("labels_total", ZebraZT411Adapter.OIDS)

    @patch.object(ZebraZT411Adapter, "_snmp_get_retry")
    @patch.object(ZebraZT411Adapter, "_snmp_get")
    def test_full_response(self, mock_get: MagicMock, mock_retry: MagicMock) -> None:
        """ZT411 with all OIDs available."""
        mock_get.return_value = (b"ZTC ZT411-203dpi ZPL", TAG_OCTET_STRING)
        mock_retry.side_effect = [
            (15234, TAG_COUNTER32),  # labels
            (761700, TAG_COUNTER32),  # meters (cm)
        ]
        result = self._make_adapter().get_counters()

        self.assertTrue(result["reachable"])
        self.assertEqual(result["labels_total"], 15234)
        self.assertAlmostEqual(result["meters_total"], 7617.0)  # 761700 cm → 7617.0 m
        self.assertEqual(result["meter_unit"], "m")
        self.assertEqual(result["model_name"], "ZTC ZT411-203dpi ZPL")

    @patch.object(ZebraZT411Adapter, "_snmp_get_retry")
    @patch.object(ZebraZT411Adapter, "_snmp_get")
    def test_labels_only(self, mock_get: MagicMock, mock_retry: MagicMock) -> None:
        """ZT411 with labels but no meters."""
        mock_get.return_value = (b"ZTC ZT411", TAG_OCTET_STRING)
        mock_retry.side_effect = [
            (8901, TAG_COUNTER32),  # labels
            (None, None),  # meters timeout
        ]
        result = self._make_adapter().get_counters()

        self.assertTrue(result["reachable"])
        self.assertEqual(result["labels_total"], 8901)
        self.assertIsNone(result["meters_total"])
        self.assertEqual(result["meter_unit"], "m")

    @patch.object(ZebraZT411Adapter, "_snmp_get")
    def test_unreachable_printer(self, mock_get: MagicMock) -> None:
        """Printer is off the network."""
        mock_get.return_value = (None, None)
        result = self._make_adapter().get_counters()

        self.assertFalse(result["reachable"])
        self.assertIsNone(result["labels_total"])
        self.assertIsNone(result["meters_total"])
        self.assertEqual(result["model_name"], "")

    @patch.object(ZebraZT411Adapter, "_snmp_get_retry")
    @patch.object(ZebraZT411Adapter, "_snmp_get")
    def test_partial_oid_response(self, mock_get: MagicMock, mock_retry: MagicMock) -> None:
        """Some OIDs respond, others timeout."""
        mock_get.return_value = (b"Zebra", TAG_OCTET_STRING)
        mock_retry.side_effect = [
            (None, None),  # labels timeout
            (None, None),  # meters timeout
        ]
        result = self._make_adapter().get_counters()

        self.assertTrue(result["reachable"])
        self.assertIsNone(result["labels_total"])
        self.assertIsNone(result["meters_total"])
        self.assertEqual(result["model_name"], "Zebra")

    @patch.object(ZebraZT411Adapter, "_snmp_get_retry")
    @patch.object(ZebraZT411Adapter, "_snmp_get")
    def test_garbage_model_name(self, mock_get: MagicMock, mock_retry: MagicMock) -> None:
        """Printer returns non-standard model name with null bytes."""
        mock_get.return_value = (b"UNKNOWN\x00\x01\x02", TAG_OCTET_STRING)
        mock_retry.side_effect = [(None, None), (None, None)]
        result = self._make_adapter().get_counters()

        self.assertTrue(result["reachable"])
        self.assertIn("UNKNOWN", result["model_name"])

    @patch.object(ZebraZT411Adapter, "_snmp_get")
    def test_is_reachable_true(self, mock_get: MagicMock) -> None:
        """is_reachable returns True when poke succeeds."""
        mock_get.return_value = (b"ZTC ZT411", TAG_OCTET_STRING)
        adapter = self._make_adapter()
        self.assertTrue(adapter.is_reachable())

    @patch.object(ZebraZT411Adapter, "_snmp_get")
    def test_is_reachable_false(self, mock_get: MagicMock) -> None:
        """is_reachable returns False when poke fails."""
        mock_get.return_value = (None, None)
        adapter = self._make_adapter()
        self.assertFalse(adapter.is_reachable())

    def test_custom_ip(self) -> None:
        """Adapter stores custom IP."""
        adapter = ZebraZT411Adapter("10.0.0.99")
        self.assertEqual(adapter.ip, "10.0.0.99")

    def test_custom_community(self) -> None:
        """Adapter stores custom community."""
        adapter = ZebraZT411Adapter("10.0.0.99", community="private")
        self.assertEqual(adapter.community, "private")


if __name__ == "__main__":
    unittest.main()
