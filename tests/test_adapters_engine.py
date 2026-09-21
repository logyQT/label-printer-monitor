"""Tests for the declarative adapter engine in adapters/base.py.

Covers:
- Generic get_counters() flow (poke -> metrics -> result contract)
- Built-in converters (int/float/str/regex/map/callable)
- SNMP version resolution (None -> snmp_version)
- _collect_extra() hook
- get_counters() result contract for every registered adapter
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import cast

from src.adapters import ADAPTER_CLASSES
from src.adapters.base import (
    METRIC_KEYS,
    ConverterSpec,
    Metric,
    PrinterAdapter,
    convert_regex,
    convert_unit_map,
    convert_value,
)
from src.snmp_client import TAG_COUNTER32, TAG_OCTET_STRING

USAGE_PATTERN = r"(\d[\d,]*)\s*(CENTIMETERS|INCHES)"


class DemoAdapter(PrinterAdapter):
    """Declarative adapter used to exercise the generic engine."""

    model_prefixes = ("demo",)
    snmp_version = 1
    reachability_oid = "1.3.6.1.2.1.1.1.0"
    metrics = (
        Metric("labels_total", oid="1.3.6.1.4.1.999.1.0", convert="int"),
        Metric("meters_total", oid="1.3.6.1.4.1.999.2.0", convert="float", unit="cm"),
    )


class OneShotAdapter(PrinterAdapter):
    """Adapter whose only metric is read without retry."""

    model_prefixes = ("oneshot",)
    reachability_oid = "1.3.6.1.2.1.1.1.0"
    metrics = (Metric("model_name", oid="1.3.6.1.4.1.999.3.0", convert="str", retry=False),)


class CollectExtraAdapter(PrinterAdapter):
    """Adapter exercising the _collect_extra hook."""

    model_prefixes = ("extra",)
    reachability_oid = "1.3.6.1.2.1.1.1.0"

    def _collect_extra(self, result: dict[str, object]) -> None:
        result["extra_field"] = "yes"


class TestGenericGetCounters(unittest.TestCase):
    """Tests for the shared get_counters() flow."""

    @patch.object(DemoAdapter, "_snmp_get")
    def test_unreachable_returns_contract(self, mock_get: MagicMock) -> None:
        """Poke failing returns the contract keys with offline defaults."""
        mock_get.return_value = (None, None)
        result = DemoAdapter("10.0.0.1").get_counters()

        self.assertEqual(set(result), set(METRIC_KEYS))
        self.assertFalse(result["reachable"])
        self.assertIsNone(result["labels_total"])
        self.assertIsNone(result["meters_total"])
        self.assertEqual(result["meter_unit"], "unknown")
        self.assertEqual(result["model_name"], "")

    @patch.object(DemoAdapter, "_snmp_get_retry")
    @patch.object(DemoAdapter, "_snmp_get")
    def test_full_collection(self, mock_get: MagicMock, mock_retry: MagicMock) -> None:
        """Reachable printer with all metrics responding."""
        mock_get.return_value = (b"Demo Printer", TAG_OCTET_STRING)
        mock_retry.side_effect = [
            (10, TAG_COUNTER32),  # labels
            (250.5, TAG_COUNTER32),  # meters
        ]
        result = DemoAdapter("10.0.0.1").get_counters()

        self.assertTrue(result["reachable"])
        self.assertEqual(result["model_name"], "Demo Printer")
        self.assertEqual(result["labels_total"], 10)
        self.assertEqual(result["meters_total"], 250.5)
        self.assertEqual(result["meter_unit"], "cm")

    @patch.object(DemoAdapter, "_snmp_get_retry")
    @patch.object(DemoAdapter, "_snmp_get")
    def test_partial_metrics(self, mock_get: MagicMock, mock_retry: MagicMock) -> None:
        """Some metrics fail; unit stays unknown without a meter reading."""
        mock_get.return_value = (b"Demo", TAG_OCTET_STRING)
        mock_retry.side_effect = [(None, None), (100.0, TAG_COUNTER32)]
        result = DemoAdapter("10.0.0.1").get_counters()

        self.assertTrue(result["reachable"])
        self.assertIsNone(result["labels_total"])
        self.assertEqual(result["meters_total"], 100.0)
        self.assertEqual(result["meter_unit"], "cm")

    @patch.object(OneShotAdapter, "_snmp_get_retry")
    @patch.object(OneShotAdapter, "_snmp_get")
    def test_retry_false_uses_single_get(self, mock_get: MagicMock, mock_retry: MagicMock) -> None:
        """retry=False reads via _snmp_get, never _snmp_get_retry."""
        mock_get.side_effect = [
            (b"One Shot", TAG_OCTET_STRING),  # poke
            (b"One Shot", TAG_OCTET_STRING),  # metric
        ]
        result = OneShotAdapter("10.0.0.1").get_counters()

        self.assertEqual(result["model_name"], "One Shot")
        mock_retry.assert_not_called()


class TestConverters(unittest.TestCase):
    """Tests for convert_value() and the built-in converters."""

    def test_passthrough(self) -> None:
        self.assertEqual(convert_value(42, None), 42)

    def test_int(self) -> None:
        self.assertEqual(convert_value(1234, "int"), 1234)

    def test_float(self) -> None:
        self.assertEqual(convert_value(761700, "float"), 761700.0)

    def test_str_decodes_bytes(self) -> None:
        self.assertEqual(convert_value(b"abc\x00", "str"), "abc\x00")

    def test_str_passes_str_through(self) -> None:
        self.assertEqual(convert_value("abc", "str"), "abc")

    def test_callable(self) -> None:
        self.assertEqual(convert_value(5, lambda v: v * 2), 10)

    def test_unknown_converter_raises(self) -> None:
        with self.assertRaises(ValueError):
            convert_value(1, cast("ConverterSpec", "bogus"))

    def test_unknown_tuple_kind_raises(self) -> None:
        with self.assertRaises(ValueError):
            convert_value(1, ("bogus", "x"))


class TestRegexConverter(unittest.TestCase):
    """Tests for the usage-string regex converter."""

    def test_prefers_centimeters(self) -> None:
        """Both units present -> centimeters win (227775 cm = 2277.75 m)."""
        self.assertEqual(convert_regex(b"89667 INCHES, 227775 CENTIMETERS", USAGE_PATTERN), 2277.75)

    def test_inches_only(self) -> None:
        result = convert_regex("1000 INCHES", USAGE_PATTERN)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result, 25.4, places=2)  # type: ignore[arg-type]

    def test_centimeters_only(self) -> None:
        self.assertEqual(convert_regex("2500 CENTIMETERS", USAGE_PATTERN), 25.0)

    def test_thousands_separator(self) -> None:
        self.assertEqual(convert_regex(b"1,000,000 CENTIMETERS", USAGE_PATTERN), 10000.0)

    def test_garbage_returns_none(self) -> None:
        self.assertIsNone(convert_regex(b"no numbers here", USAGE_PATTERN))


class TestUnitMapConverter(unittest.TestCase):
    """Tests for the marker-unit-code map converter."""

    MAP = {"5": "linearMeters", "17": "m"}

    def test_known_code(self) -> None:
        self.assertEqual(convert_unit_map(5, self.MAP), "linearMeters")

    def test_custom_code(self) -> None:
        self.assertEqual(convert_unit_map(17, self.MAP), "m")

    def test_unknown_code_fallback(self) -> None:
        self.assertEqual(convert_unit_map(7, self.MAP), "unit_code:7")

    def test_custom_fallback(self) -> None:
        self.assertEqual(convert_unit_map(7, self.MAP, "code:{}"), "code:7")


class TestVersionResolution(unittest.TestCase):
    """Adapters own their SNMP version unless explicitly overridden."""

    def test_none_uses_class_default(self) -> None:
        self.assertEqual(DemoAdapter("10.0.0.1").version, 1)

    def test_explicit_overrides_class_default(self) -> None:
        self.assertEqual(DemoAdapter("10.0.0.1", version=0).version, 0)

    def test_every_registered_adapter_resolves_its_version(self) -> None:
        for cls in ADAPTER_CLASSES:
            with self.subTest(adapter=cls.__name__):
                self.assertEqual(cls("10.0.0.1").version, cls.snmp_version)


class TestCollectExtraHook(unittest.TestCase):
    """_collect_extra() runs on reachable printers."""

    @patch.object(CollectExtraAdapter, "_snmp_get")
    def test_hook_runs(self, mock_get: MagicMock) -> None:
        mock_get.return_value = (b"X", TAG_OCTET_STRING)
        result = CollectExtraAdapter("10.0.0.1").get_counters()
        self.assertEqual(result.get("extra_field"), "yes")


class TestAdapterContract(unittest.TestCase):
    """Every registered adapter must honor the shared result contract."""

    def test_all_adapters_have_registry_metadata(self) -> None:
        self.assertTrue(ADAPTER_CLASSES)
        for cls in ADAPTER_CLASSES:
            with self.subTest(adapter=cls.__name__):
                self.assertTrue(cls.model_prefixes)
                self.assertTrue(cls.reachability_oid)
                self.assertIsInstance(cls.metrics, tuple)

    def test_unreachable_result_contract(self) -> None:
        """Poke failure -> exactly METRIC_KEYS with offline defaults."""
        for cls in ADAPTER_CLASSES:
            with self.subTest(adapter=cls.__name__):
                adapter = cls("10.0.0.1", retries=0)
                with patch.object(cls, "_snmp_get", return_value=(None, None)):
                    result = adapter.get_counters()
                self.assertEqual(set(result), set(METRIC_KEYS))
                self.assertFalse(result["reachable"])
                self.assertEqual(result["meter_unit"], "unknown")
                self.assertEqual(result["model_name"], "")

    def test_reachable_no_metrics_result_contract(self) -> None:
        """Reachable but all metric reads fail -> keys present, unit unknown."""
        for cls in ADAPTER_CLASSES:
            with self.subTest(adapter=cls.__name__):
                adapter = cls("10.0.0.1", retries=0)
                with (
                    patch.object(cls, "_snmp_get", return_value=(b"ZZZ\x00x", TAG_OCTET_STRING)),
                    patch.object(cls, "_snmp_get_retry", return_value=(None, None)),
                ):
                    result = adapter.get_counters()
                self.assertEqual(set(result), set(METRIC_KEYS))
                self.assertTrue(result["reachable"])
                self.assertIn("ZZZ", result["model_name"])
                self.assertIsNone(result["labels_total"])
                self.assertIsNone(result["meters_total"])
                self.assertEqual(result["meter_unit"], "unknown")


if __name__ == "__main__":
    unittest.main()
