"""Tests for validate.py - setup validation and network checks."""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # src/
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)  # project root

from typing import Any

import src.validate as validate


class FakeAdapter:
    """Deterministic fake: only the .1 printer is reachable."""

    def __init__(self, ip: str, **kwargs: Any) -> None:
        self.ip = ip

    def is_reachable(self) -> bool:
        return self.ip == "10.0.0.1"


class TestValidateNetwork(unittest.TestCase):
    """Tests for validate_network() with real SNMP patched out."""

    def _config(self, **overrides: Any) -> dict[str, Any]:
        cfg = {
            "snmp": {"community": "public", "timeout_sec": 1, "retries": 0},
            "printers": [
                {"ip": "10.0.0.1", "model": "Zebra ZT411", "location": "Line 1"},
                {"ip": "10.0.0.2", "model": "Zebra ZT411", "location": "Line 2"},
                {"ip": "10.0.0.3", "model": "Zebra ZT411", "location": "Line 3"},
            ],
        }
        cfg.update(overrides)
        return cfg

    def test_checks_all_printers_in_order(self) -> None:
        with patch("src.validate.get_adapter_class", return_value=FakeAdapter):
            issues = validate.validate_network(self._config())
        self.assertEqual(len(issues), 3)
        self.assertIn("SNMP reachable", issues[0].message)
        self.assertIn("10.0.0.1", issues[0].message)
        self.assertIn("NOT reachable", issues[1].message)
        self.assertIn("10.0.0.2", issues[1].message)
        self.assertIn("NOT reachable", issues[2].message)
        self.assertIn("10.0.0.3", issues[2].message)

    def test_empty_printers_returns_empty(self) -> None:
        issues = validate.validate_network({"snmp": {}, "printers": []})
        self.assertEqual(issues, [])

    def test_uses_collection_concurrency(self) -> None:
        from concurrent.futures import ThreadPoolExecutor as RealTPE

        captured = {}

        def fake_tpe(max_workers: int | None = None, **kwargs: Any) -> Any:
            captured["max_workers"] = max_workers
            return RealTPE(max_workers=max_workers)

        cfg = self._config(collection={"max_concurrency": 3})
        with (
            patch("src.validate.get_adapter_class", return_value=FakeAdapter),
            patch("src.validate.concurrent.futures.ThreadPoolExecutor", side_effect=fake_tpe),
        ):
            issues = validate.validate_network(cfg)
        self.assertEqual(captured.get("max_workers"), 3)
        self.assertEqual(len(issues), 3)

    def test_raises_are_reported_not_fatal(self) -> None:
        class ExplodingAdapter:
            def __init__(self, ip: str, **kwargs: Any) -> None:
                pass

            def is_reachable(self) -> bool:
                raise RuntimeError("boom")

        with patch("src.validate.get_adapter_class", return_value=ExplodingAdapter):
            issues = validate.validate_network(self._config())
        self.assertEqual(len(issues), 3)
        self.assertTrue(all("check failed" in i.message for i in issues))


if __name__ == "__main__":
    unittest.main()
