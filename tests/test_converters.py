"""Tests for src/converters.py - unit conversion functions.

All metric values are standardized to meters at collection time.
These tests verify every converter and the central to_meters() entry point.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.converters import cm_to_m, ft_to_m, in_to_m, mm_to_m, to_meters


class TestCmToM(unittest.TestCase):
    def test_basic(self) -> None:
        self.assertAlmostEqual(cm_to_m(100), 1.0)

    def test_zero(self) -> None:
        self.assertEqual(cm_to_m(0), 0.0)

    def test_fractional(self) -> None:
        self.assertAlmostEqual(cm_to_m(250.5), 2.505)


class TestMmToM(unittest.TestCase):
    def test_basic(self) -> None:
        self.assertAlmostEqual(mm_to_m(1000), 1.0)

    def test_zero(self) -> None:
        self.assertEqual(mm_to_m(0), 0.0)


class TestInToM(unittest.TestCase):
    def test_basic(self) -> None:
        self.assertAlmostEqual(in_to_m(1), 0.0254)

    def test_twelve_inches(self) -> None:
        self.assertAlmostEqual(in_to_m(12), 0.3048)


class TestFtToM(unittest.TestCase):
    def test_basic(self) -> None:
        self.assertAlmostEqual(ft_to_m(1), 0.3048)

    def test_hundred_feet(self) -> None:
        self.assertAlmostEqual(ft_to_m(100), 30.48)


class TestToMeters(unittest.TestCase):
    """Tests for the central to_meters() entry point."""

    def test_none_returns_none(self) -> None:
        self.assertIsNone(to_meters(None, "cm"))

    def test_m_passthrough(self) -> None:
        self.assertAlmostEqual(to_meters(12.5, "m"), 12.5)

    def test_linear_meters_passthrough(self) -> None:
        self.assertAlmostEqual(to_meters(123456.0, "linearMeters"), 123456.0)

    def test_cm_conversion(self) -> None:
        self.assertAlmostEqual(to_meters(761700, "cm"), 7617.0)

    def test_mm_conversion(self) -> None:
        self.assertAlmostEqual(to_meters(1000, "mm"), 1.0)

    def test_inches_conversion(self) -> None:
        self.assertAlmostEqual(to_meters(1000, "inches"), 25.4)

    def test_linear_feet_conversion(self) -> None:
        self.assertAlmostEqual(to_meters(100, "linearFeet"), 30.48)

    def test_unknown_unit_passes_through(self) -> None:
        """Unknown units pass through unchanged (callers should log a warning)."""
        self.assertAlmostEqual(to_meters(42, "bogus"), 42.0)

    def test_int_input(self) -> None:
        """Integer input is accepted and returns a float."""
        result = to_meters(100, "cm")
        self.assertIsInstance(result, float)
        self.assertAlmostEqual(result, 1.0)


if __name__ == "__main__":
    unittest.main()
