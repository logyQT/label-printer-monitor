"""Tests for adapters/__init__.py - adapter registry.

Tests cover:
- Adapter registration for all models
- Model prefix resolution
- Adapter creation
- Error handling for unknown models
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.adapters import ADAPTER_CLASSES, ADAPTER_REGISTRY, create_adapter, get_adapter_class
from src.adapters.sato_cl4nx_plus import SatoCL4NXPlusAdapter
from src.adapters.zebra_gx430t import ZebraGX430tAdapter
from src.adapters.zebra_zt411 import ZebraZT411Adapter


class TestAdapterRegistry(unittest.TestCase):
    """Tests for ADAPTER_REGISTRY."""

    def test_registry_not_empty(self) -> None:
        self.assertGreater(len(ADAPTER_REGISTRY), 0)

    def test_registry_matches_declared_prefixes(self) -> None:
        """Registry is exactly what the adapter classes declare - no drift."""
        expected = {p: cls for cls in ADAPTER_CLASSES for p in cls.model_prefixes}
        self.assertEqual(ADAPTER_REGISTRY, expected)

    def test_zebra_zt411_registered(self) -> None:
        self.assertIn("zebra zt411", ADAPTER_REGISTRY)

    def test_zebra_gx430t_registered(self) -> None:
        self.assertIn("zebra gx430t", ADAPTER_REGISTRY)

    def test_sato_cl4nx_plus_registered(self) -> None:
        self.assertIn("sato cl4nx plus", ADAPTER_REGISTRY)

    def test_zebra_zt411_class(self) -> None:
        self.assertEqual(ADAPTER_REGISTRY["zebra zt411"], ZebraZT411Adapter)

    def test_zebra_gx430t_class(self) -> None:
        self.assertEqual(ADAPTER_REGISTRY["zebra gx430t"], ZebraGX430tAdapter)

    def test_sato_cl4nx_plus_class(self) -> None:
        self.assertEqual(ADAPTER_REGISTRY["sato cl4nx plus"], SatoCL4NXPlusAdapter)


class TestGetAdapterClass(unittest.TestCase):
    """Tests for get_adapter_class()."""

    def test_zebra_zt411(self) -> None:
        cls = get_adapter_class("Zebra ZT411")
        self.assertEqual(cls, ZebraZT411Adapter)

    def test_zebra_gx430t(self) -> None:
        cls = get_adapter_class("Zebra GX430t")
        self.assertEqual(cls, ZebraGX430tAdapter)

    def test_sato_cl4nx_plus(self) -> None:
        cls = get_adapter_class("Sato CL4NX Plus")
        self.assertEqual(cls, SatoCL4NXPlusAdapter)

    def test_case_insensitive_zt411(self) -> None:
        cls = get_adapter_class("zebra zt411")
        self.assertEqual(cls, ZebraZT411Adapter)

    def test_case_insensitive_gx430t(self) -> None:
        cls = get_adapter_class("ZEBRA GX430T")
        self.assertEqual(cls, ZebraGX430tAdapter)

    def test_case_insensitive_sato(self) -> None:
        cls = get_adapter_class("SATO CL4NX PLUS")
        self.assertEqual(cls, SatoCL4NXPlusAdapter)

    def test_unknown_model_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            get_adapter_class("HP LaserJet")
        self.assertIn("HP LaserJet", str(ctx.exception))

    def test_empty_string_raises(self) -> None:
        with self.assertRaises(ValueError):
            get_adapter_class("")

    def test_partial_match_zebra(self) -> None:
        cls = get_adapter_class("Zebra ZT411 Industrial")
        self.assertEqual(cls, ZebraZT411Adapter)

    def test_partial_match_sato(self) -> None:
        cls = get_adapter_class("Sato CL4NX Plus v2")
        self.assertEqual(cls, SatoCL4NXPlusAdapter)


class TestCreateAdapter(unittest.TestCase):
    """Tests for create_adapter()."""

    def test_create_zebra_zt411(self) -> None:
        adapter = create_adapter("Zebra ZT411", "10.0.0.1")
        self.assertIsInstance(adapter, ZebraZT411Adapter)
        self.assertEqual(adapter.ip, "10.0.0.1")

    def test_create_zebra_gx430t(self) -> None:
        adapter = create_adapter("Zebra GX430t", "10.0.0.2")
        self.assertIsInstance(adapter, ZebraGX430tAdapter)
        self.assertEqual(adapter.ip, "10.0.0.2")

    def test_create_sato_cl4nx_plus(self) -> None:
        adapter = create_adapter("Sato CL4NX Plus", "10.0.0.3")
        self.assertIsInstance(adapter, SatoCL4NXPlusAdapter)
        self.assertEqual(adapter.ip, "10.0.0.3")

    def test_create_with_community(self) -> None:
        adapter = create_adapter("Zebra ZT411", "10.0.0.1", community="private")
        self.assertEqual(adapter.community, "private")

    def test_create_with_timeout(self) -> None:
        adapter = create_adapter("Zebra ZT411", "10.0.0.1", timeout_sec=10)
        self.assertEqual(adapter.timeout_sec, 10)

    def test_create_with_retries(self) -> None:
        adapter = create_adapter("Zebra ZT411", "10.0.0.1", retries=5)
        self.assertEqual(adapter.retries, 5)

    def test_unknown_model_raises(self) -> None:
        with self.assertRaises(ValueError):
            create_adapter("Unknown Model", "10.0.0.1")


if __name__ == "__main__":
    unittest.main()
