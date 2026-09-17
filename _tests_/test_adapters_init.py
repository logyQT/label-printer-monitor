"""Tests for adapters/__init__.py - adapter registry.

Tests cover:
- Adapter registration
- Model prefix resolution
- Adapter creation
- Error handling for unknown models
"""

import sys
import os
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters import get_adapter_class, create_adapter, ADAPTER_REGISTRY
from adapters.zebra import ZebraAdapter


class TestAdapterRegistry(unittest.TestCase):
    """Tests for ADAPTER_REGISTRY."""

    def test_registry_not_empty(self):
        self.assertGreater(len(ADAPTER_REGISTRY), 0)

    def test_zebra_registered(self):
        self.assertIn('zebra', ADAPTER_REGISTRY)

    def test_zebra_class(self):
        self.assertEqual(ADAPTER_REGISTRY['zebra'], ZebraAdapter)


class TestGetAdapterClass(unittest.TestCase):
    """Tests for get_adapter_class()."""

    def test_zebra_zt230(self):
        cls = get_adapter_class('Zebra ZT230')
        self.assertEqual(cls, ZebraAdapter)

    def test_zebra_zt411(self):
        cls = get_adapter_class('Zebra ZT411')
        self.assertEqual(cls, ZebraAdapter)

    def test_zebra_gx430t(self):
        cls = get_adapter_class('Zebra GX430t')
        self.assertEqual(cls, ZebraAdapter)

    def test_zebra_zd621(self):
        cls = get_adapter_class('Zebra ZD621')
        self.assertEqual(cls, ZebraAdapter)

    def test_case_insensitive(self):
        cls = get_adapter_class('zebra zt230')
        self.assertEqual(cls, ZebraAdapter)

    def test_mixed_case(self):
        cls = get_adapter_class('ZEBRA ZT230')
        self.assertEqual(cls, ZebraAdapter)

    def test_unknown_model_raises(self):
        with self.assertRaises(ValueError) as ctx:
            get_adapter_class('HP LaserJet')
        self.assertIn('HP LaserJet', str(ctx.exception))

    def test_empty_string_raises(self):
        with self.assertRaises(ValueError):
            get_adapter_class('')

    def test_partial_match(self):
        cls = get_adapter_class('Zebra ZT230 Industrial')
        self.assertEqual(cls, ZebraAdapter)


class TestCreateAdapter(unittest.TestCase):
    """Tests for create_adapter()."""

    def test_create_zebra_adapter(self):
        adapter = create_adapter('Zebra ZT230', '10.0.0.1')
        self.assertIsInstance(adapter, ZebraAdapter)
        self.assertEqual(adapter.ip, '10.0.0.1')

    def test_create_with_community(self):
        adapter = create_adapter('Zebra ZT230', '10.0.0.1', community='private')
        self.assertEqual(adapter.community, 'private')

    def test_create_with_timeout(self):
        adapter = create_adapter('Zebra ZT230', '10.0.0.1', timeout_sec=5)
        self.assertEqual(adapter.timeout_sec, 5)

    def test_create_with_retries(self):
        adapter = create_adapter('Zebra ZT230', '10.0.0.1', retries=3)
        self.assertEqual(adapter.retries, 3)

    def test_create_all_models(self):
        models = ['Zebra ZT230', 'Zebra ZT411', 'Zebra GX430t', 'Zebra ZD621']
        for model in models:
            adapter = create_adapter(model, '10.0.0.1')
            self.assertIsInstance(adapter, ZebraAdapter)

    def test_unknown_model_raises(self):
        with self.assertRaises(ValueError):
            create_adapter('Unknown Model', '10.0.0.1')


if __name__ == '__main__':
    unittest.main()
