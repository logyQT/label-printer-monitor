"""Unit converters for printer metric standardization.

All metric values are standardized to **meters** at collection time.
These converters are the single source of truth for unit → meters math.
"""

from collections.abc import Callable

# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def cm_to_m(value: float) -> float:
    """Centimeters → meters."""
    return value / 100.0


def mm_to_m(value: float) -> float:
    """Millimeters → meters."""
    return value / 1000.0


def in_to_m(value: float) -> float:
    """Inches → meters."""
    return value * 0.0254


def ft_to_m(value: float) -> float:
    """Feet → meters."""
    return value * 0.3048


# ---------------------------------------------------------------------------
# Lookup table  (unit string → converter function)
# ---------------------------------------------------------------------------

_UNIT_TO_M: dict[str, Callable[[float], float]] = {
    "m": lambda v: v,  # already meters – passthrough
    "linearMeters": lambda v: v,  # Sato alias for meters
    "cm": cm_to_m,
    "centimeters": cm_to_m,
    "mm": mm_to_m,
    "millimeters": mm_to_m,
    "in": in_to_m,
    "inches": in_to_m,
    "linearFeet": ft_to_m,
}


def to_meters(value: float | int | None, unit: str) -> float | None:
    """Convert *value* from *unit* to meters.

    Returns ``None`` when *value* is ``None`` or *unit* is unknown
    (unknown units are passed through unchanged so callers don't silently
    lose data - they should log a warning).

    Examples::

        >>> to_meters(150, "cm")
        1.5
        >>> to_meters(1000, "mm")
        1.0
        >>> to_meters(12.5, "m")
        12.5
    """
    if value is None:
        return None
    converter = _UNIT_TO_M.get(unit)
    if converter is None:
        return float(value)  # unknown unit – pass through
    return converter(float(value))
