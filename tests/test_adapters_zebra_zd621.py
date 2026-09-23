"""Tests for adapters/zebra_zd621.py - Zebra ZD621 adapter (experimental).

The ZD621 adapter reuses the Link-OS ZQL "200" range OIDs (verified on a
live ZT411, see zebra_zt411.py).  Tests cover:
- Poke (reachability via model name OID)
- Label counter (STRING counter)
- Print length ("XX INCHES, XX CENTIMETERS" -> meters)
- SNMPv2c by default
- Unreachable printer
- The OIDS dict derived from the metrics
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.adapters.zebra_zd621 import OID_LABELS, OID_METERS, OID_REACHABILITY, ZebraZD621Adapter
from src.snmp_client import TAG_OCTET_STRING


class TestZebraZD621Adapter(unittest.TestCase):
    """Tests for ZebraZD621Adapter (experimental)."""

    def _make_adapter(self, ip: str = "192.168.40.30") -> ZebraZD621Adapter:
        return ZebraZD621Adapter(ip, community="public", timeout_sec=3, retries=0)

    def test_default_version_is_v2c(self) -> None:
        """ZD621 defaults to SNMPv2c (version=1)."""
        adapter = self._make_adapter()
        self.assertEqual(adapter.version, 1)

    def test_oids_are_linkos_200_range(self) -> None:
        """All three OIDs live in the Link-OS '200' range."""
        self.assertEqual(OID_REACHABILITY, "1.3.6.1.4.1.10642.200.19.7.0")
        self.assertEqual(OID_LABELS, "1.3.6.1.4.1.10642.200.17.2.0")
        self.assertEqual(OID_METERS, "1.3.6.1.4.1.10642.200.17.3.0")

    def test_class_oids(self) -> None:
        """OIDS is derived from the declared metrics."""
        self.assertEqual(
            ZebraZD621Adapter.OIDS,
            {"labels_total": OID_LABELS, "meters_total": OID_METERS},
        )

    def test_model_prefix(self) -> None:
        """Registry prefix matches the config model string."""
        self.assertIn("zebra zd621", ZebraZD621Adapter.model_prefixes)

    @patch.object(ZebraZD621Adapter, "_snmp_get_retry")
    @patch.object(ZebraZD621Adapter, "_snmp_get")
    def test_full_response(self, mock_get: MagicMock, mock_retry: MagicMock) -> None:
        """ZD621 with all OIDs available (STRING counters, usage string)."""
        mock_get.return_value = (b"ZD621", TAG_OCTET_STRING)
        mock_retry.side_effect = [
            (b"302318", TAG_OCTET_STRING),  # total label count (STRING)
            (b"555719 INCHES, 1411527 CENTIMETERS", TAG_OCTET_STRING),  # print length
        ]
        result = self._make_adapter().get_counters()

        self.assertTrue(result["reachable"])
        self.assertEqual(result["labels_total"], 302318)
        self.assertAlmostEqual(result["meters_total"], 14115.27, places=2)  # 1411527 cm
        self.assertEqual(result["meter_unit"], "m")
        self.assertEqual(result["model_name"], "ZD621")

    @patch.object(ZebraZD621Adapter, "_snmp_get_retry")
    @patch.object(ZebraZD621Adapter, "_snmp_get")
    def test_labels_only(self, mock_get: MagicMock, mock_retry: MagicMock) -> None:
        """Labels but no print length."""
        mock_get.return_value = (b"ZD621", TAG_OCTET_STRING)
        mock_retry.side_effect = [
            (b"8901", TAG_OCTET_STRING),
            (None, None),
        ]
        result = self._make_adapter().get_counters()

        self.assertTrue(result["reachable"])
        self.assertEqual(result["labels_total"], 8901)
        self.assertIsNone(result["meters_total"])

    @patch.object(ZebraZD621Adapter, "_snmp_get_retry")
    @patch.object(ZebraZD621Adapter, "_snmp_get")
    def test_poke_and_metrics_called(self, mock_get: MagicMock, mock_retry: MagicMock) -> None:
        """get_counters pokes the model OID, then reads labels + print length."""
        mock_get.return_value = (b"ZD621", TAG_OCTET_STRING)
        mock_retry.side_effect = [
            (b"1", TAG_OCTET_STRING),
            (b"1 INCHES, 1 CENTIMETERS", TAG_OCTET_STRING),
        ]
        self._make_adapter().get_counters()

        mock_get.assert_called_with(OID_REACHABILITY, label="poke")
        labels_call, meters_call = mock_retry.call_args_list
        self.assertEqual(labels_call[0][0], OID_LABELS)
        self.assertEqual(meters_call[0][0], OID_METERS)

    @patch.object(ZebraZD621Adapter, "_snmp_get")
    def test_unreachable_printer(self, mock_get: MagicMock) -> None:
        """Printer is off the network."""
        mock_get.return_value = (None, None)
        result = self._make_adapter().get_counters()

        self.assertFalse(result["reachable"])
        self.assertIsNone(result["labels_total"])
        self.assertIsNone(result["meters_total"])
        self.assertEqual(result["model_name"], "")

    @patch.object(ZebraZD621Adapter, "_snmp_get")
    def test_is_reachable_true(self, mock_get: MagicMock) -> None:
        """is_reachable returns True when poke succeeds."""
        mock_get.return_value = (b"ZD621", TAG_OCTET_STRING)
        self.assertTrue(self._make_adapter().is_reachable())

    @patch.object(ZebraZD621Adapter, "_snmp_get")
    def test_is_reachable_false(self, mock_get: MagicMock) -> None:
        """is_reachable returns False when poke fails."""
        mock_get.return_value = (None, None)
        self.assertFalse(self._make_adapter().is_reachable())

    def test_custom_ip(self) -> None:
        """Adapter stores custom IP."""
        adapter = ZebraZD621Adapter("10.0.0.99")
        self.assertEqual(adapter.ip, "10.0.0.99")

    def test_custom_community(self) -> None:
        """Adapter stores custom community."""
        adapter = ZebraZD621Adapter("10.0.0.99", community="private")
        self.assertEqual(adapter.community, "private")


if __name__ == "__main__":
    unittest.main()
