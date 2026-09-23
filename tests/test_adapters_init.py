"""Tests for adapters/__init__.py - adapter registry.

Tests cover:
- Adapter registration for all models
- Model prefix resolution
- Adapter creation
- Error handling for unknown models
- Every model in config.json.schema resolves to an adapter
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.adapters import ADAPTER_CLASSES, ADAPTER_REGISTRY, create_adapter, get_adapter_class
from src.adapters.sato_cl4nx_plus import SatoCL4NXPlusAdapter
from src.adapters.zebra_gx430t import ZebraGX430tAdapter
from src.adapters.zebra_zd621 import ZebraZD621Adapter
from src.adapters.zebra_zt230 import ZebraZT230Adapter
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

    def test_zebra_zd621_registered(self) -> None:
        self.assertIn("zebra zd621", ADAPTER_REGISTRY)

    def test_zebra_zt230_registered(self) -> None:
        self.assertIn("zebra zt230", ADAPTER_REGISTRY)

    def test_zebra_zt411_class(self) -> None:
        self.assertEqual(ADAPTER_REGISTRY["zebra zt411"], ZebraZT411Adapter)

    def test_zebra_gx430t_class(self) -> None:
        self.assertEqual(ADAPTER_REGISTRY["zebra gx430t"], ZebraGX430tAdapter)

    def test_sato_cl4nx_plus_class(self) -> None:
        self.assertEqual(ADAPTER_REGISTRY["sato cl4nx plus"], SatoCL4NXPlusAdapter)

    def test_zebra_zd621_class(self) -> None:
        self.assertEqual(ADAPTER_REGISTRY["zebra zd621"], ZebraZD621Adapter)

    def test_zebra_zt230_class(self) -> None:
        self.assertEqual(ADAPTER_REGISTRY["zebra zt230"], ZebraZT230Adapter)


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

    def test_zebra_zd621(self) -> None:
        cls = get_adapter_class("Zebra ZD621")
        self.assertEqual(cls, ZebraZD621Adapter)

    def test_zebra_zt230(self) -> None:
        cls = get_adapter_class("Zebra ZT230")
        self.assertEqual(cls, ZebraZT230Adapter)

    def test_case_insensitive_zt411(self) -> None:
        cls = get_adapter_class("zebra zt411")
        self.assertEqual(cls, ZebraZT411Adapter)

    def test_case_insensitive_gx430t(self) -> None:
        cls = get_adapter_class("ZEBRA GX430T")
        self.assertEqual(cls, ZebraGX430tAdapter)

    def test_case_insensitive_sato(self) -> None:
        cls = get_adapter_class("SATO CL4NX PLUS")
        self.assertEqual(cls, SatoCL4NXPlusAdapter)

    def test_case_insensitive_zd621(self) -> None:
        cls = get_adapter_class("ZEBRA ZD621")
        self.assertEqual(cls, ZebraZD621Adapter)

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

    def test_partial_match_zd621(self) -> None:
        cls = get_adapter_class("Zebra ZD621 203dpi")
        self.assertEqual(cls, ZebraZD621Adapter)


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

    def test_create_zebra_zd621(self) -> None:
        adapter = create_adapter("Zebra ZD621", "10.0.0.4")
        self.assertIsInstance(adapter, ZebraZD621Adapter)
        self.assertEqual(adapter.ip, "10.0.0.4")

    def test_create_zebra_zt230(self) -> None:
        adapter = create_adapter("Zebra ZT230", "10.0.0.5")
        self.assertIsInstance(adapter, ZebraZT230Adapter)
        self.assertEqual(adapter.ip, "10.0.0.5")

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


class TestSchemaModelsWithAdapters(unittest.TestCase):
    """Every model listed in config.json.schema must resolve to an adapter."""

    def test_every_schema_model_resolves(self) -> None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        schema_path = os.path.join(root, "config", "config.json.schema")
        with open(schema_path, encoding="utf-8") as handle:
            schema = json.load(handle)
        models = schema["properties"]["printers"]["items"]["properties"]["model"]["enum"]
        self.assertGreaterEqual(len(models), 5)
        for model in models:
            with self.subTest(model=model):
                get_adapter_class(model)  # must not raise


if __name__ == "__main__":
    unittest.main()
