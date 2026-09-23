"""Sato CL4NX Plus adapter.

Two SNMP requests total:
1. Poke (GET) - check reachability
2. Meters + unit with exponential backoff retry

No label count available via SNMP on Sato.

Declarative specification - see adapters.base for the engine.
"""

from src.adapters.base import Metric, PrinterAdapter
from src.converters import to_meters

OID_REACHABILITY: str = "1.3.6.1.2.1.43.5.1.1.16.1"  # printer name
OID_METERS: str = "1.3.6.1.2.1.43.10.2.1.4.1.1"  # prtMarkerLifeCount
OID_UNIT: str = "1.3.6.1.2.1.43.10.2.1.3.1.1"  # prtMarkerCounterUnit

# prtMarkerCounterUnit values → human-readable strings.
# These come from the printer firmware, not user config.
UNIT_MAP: dict[str, str] = {
    "3": "sheets",
    "4": "linearFeet",
    "5": "linearMeters",
    "17": "m",  # Sato custom code observed on CL4NX Plus
}


class SatoCL4NXPlusAdapter(PrinterAdapter):
    model_prefixes = ("sato cl4nx plus",)
    snmp_version = 1
    reachability_oid = OID_REACHABILITY
    # meters first: the unit read is independent, but meters is the primary
    # counter and keep the historical fetch order (and test side-effects).
    metrics = (
        Metric("meters_total", oid=OID_METERS, convert="float", label="meters"),
        Metric("meter_unit", oid=OID_UNIT, convert=("map", UNIT_MAP, "unit_code:{}"), label="unit"),
    )

    def _collect_extra(self, result: dict[str, object]) -> None:
        """Convert meters_total to meters using the raw unit detected via SNMP.

        The base engine sets meter_unit="m", but the Sato metric writes the
        firmware-reported unit (e.g. "linearMeters", "linearFeet") into
        result["meter_unit"] *after* the base sets it.  We use that raw value
        to convert meters_total, then overwrite meter_unit with "m".
        """
        raw_unit = result.get("meter_unit", "m")
        meters = result.get("meters_total")
        # Narrow the ``dict[str, object]`` result: the engine stores a float
        # for meters_total and a str for meter_unit, but only isinstance can
        # prove it to the type checker (and guards at runtime too).
        if meters is not None and isinstance(meters, (int, float)) and isinstance(raw_unit, str) and raw_unit != "m":
            result["meters_total"] = to_meters(meters, raw_unit)
        result["meter_unit"] = "m"
