"""Tests for adapters/base.py - PrinterAdapter base class."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.adapters.base import CounterResult, PrinterAdapter


class ConcretePrinterAdapter(PrinterAdapter):
    def get_counters(self) -> CounterResult:
        return CounterResult(
            labels_total=None,
            meters_total=None,
            meter_unit="m",
            model_name="",
            reachable=True,
        )

    def is_reachable(self) -> bool:
        return True


class FailingPrinterAdapter(PrinterAdapter):
    def get_counters(self) -> CounterResult:
        return CounterResult(
            labels_total=None,
            meters_total=None,
            meter_unit="m",
            model_name="",
            reachable=False,
        )

    def is_reachable(self) -> bool:
        return False


class TestSnmpGet(unittest.TestCase):
    """Tests for _snmp_get helper."""

    @patch("src.snmp_client.get")
    def test_successful_get(self, mock_get: MagicMock) -> None:
        mock_get.return_value = (42, 0x02)
        adapter = ConcretePrinterAdapter("10.0.0.1")
        value, tag = adapter._snmp_get("1.3.6.1.2.1.1.1.0")
        self.assertEqual(value, 42)
        self.assertEqual(tag, 0x02)

    @patch("src.snmp_client.get")
    def test_timeout_returns_none(self, mock_get: MagicMock) -> None:
        from src.snmp_client import SnmpTimeout

        mock_get.side_effect = SnmpTimeout("timeout")
        adapter = ConcretePrinterAdapter("10.0.0.1")
        value, tag = adapter._snmp_get("1.3.6.1.2.1.1.1.0")
        self.assertIsNone(value)
        self.assertIsNone(tag)

    @patch("src.snmp_client.get")
    def test_snmp_error_returns_none(self, mock_get: MagicMock) -> None:
        from src.snmp_client import SnmpError

        mock_get.side_effect = SnmpError("error")
        adapter = ConcretePrinterAdapter("10.0.0.1")
        value, tag = adapter._snmp_get("1.3.6.1.2.1.1.1.0")
        self.assertIsNone(value)
        self.assertIsNone(tag)


class TestSnmpGetRetry(unittest.TestCase):
    """Tests for _snmp_get_retry helper."""

    @patch("src.snmp_client.get")
    def test_success_on_first_try(self, mock_get: MagicMock) -> None:
        mock_get.return_value = (100, 0x02)
        adapter = ConcretePrinterAdapter("10.0.0.1")
        value, tag = adapter._snmp_get_retry("1.3.6.1", label="test")
        self.assertEqual(value, 100)

    @patch("time.sleep")
    @patch("src.snmp_client.get")
    def test_retries_on_timeout(self, mock_get: MagicMock, mock_sleep: MagicMock) -> None:
        from src.snmp_client import SnmpTimeout

        mock_get.side_effect = [SnmpTimeout("t"), SnmpTimeout("t"), (42, 0x02)]
        adapter = ConcretePrinterAdapter("10.0.0.1")
        value, tag = adapter._snmp_get_retry("1.3.6.1", label="test", attempts=3)
        self.assertEqual(value, 42)
        self.assertEqual(mock_get.call_count, 3)
        # Exponential backoff: 0.5, 1.0
        calls = [c[0][0] for c in mock_sleep.call_args_list]
        self.assertEqual(calls, [0.5, 1.0])

    @patch("time.sleep")
    @patch("src.snmp_client.get")
    def test_all_retries_fail(self, mock_get: MagicMock, mock_sleep: MagicMock) -> None:
        from src.snmp_client import SnmpTimeout

        mock_get.side_effect = SnmpTimeout("t")
        adapter = ConcretePrinterAdapter("10.0.0.1")
        value, tag = adapter._snmp_get_retry("1.3.6.1", label="test", attempts=3)
        self.assertIsNone(value)
        self.assertEqual(mock_get.call_count, 3)


class TestStatusFromCode(unittest.TestCase):
    def test_all_codes(self) -> None:
        self.assertEqual(PrinterAdapter._status_from_code(1), "other")
        self.assertEqual(PrinterAdapter._status_from_code(3), "idle")
        self.assertEqual(PrinterAdapter._status_from_code(4), "printing")
        self.assertEqual(PrinterAdapter._status_from_code(None), "offline")
        self.assertEqual(PrinterAdapter._status_from_code(99), "unknown")


if __name__ == "__main__":
    unittest.main()
