"""Zebra ZT230 adapter - EXPERIMENTAL.

The ZT230 sits at the edge of the Link-OS era, so this adapter assumes
the ZQL "200" range is present (same three OIDs as the verified ZT411
adapter, see zebra_zt411.py):

- 10642.200.19.7.0 = model name, e.g. "ZT230"       (poke)
- 10642.200.17.2.0 = total label count, STRING "302318"
- 10642.200.17.3.0 = total print length, "555719 INCHES, 1411527 CENTIMETERS"

No ZT230 was available to verify against yet, and older units may be
pre-Link-OS: if the printer stays unreachable, set ``snmp_version = 0``
(the GX430t of the same vintage answers SNMPv1 only).

Declarative specification - see adapters.base for the engine.
"""

from src.adapters.base import Metric, PrinterAdapter

OID_REACHABILITY: str = "1.3.6.1.4.1.10642.200.19.7.0"  # model name (poke)
OID_LABELS: str = "1.3.6.1.4.1.10642.200.17.2.0"  # total label count (STRING)
OID_METERS: str = "1.3.6.1.4.1.10642.200.17.3.0"  # "XX INCHES, XX CENTIMETERS"

# Matches "<number> CENTIMETERS" or "<number> INCHES" in the usage string.
USAGE_PATTERN: str = r"(\d[\d,]*)\s*(CENTIMETERS|INCHES)"


class ZebraZT230Adapter(PrinterAdapter):
    model_prefixes = ("zebra zt230",)
    snmp_version = 1
    reachability_oid = OID_REACHABILITY
    metrics = (
        Metric("labels_total", oid=OID_LABELS, convert="int", label="labels"),
        Metric(
            "meters_total",
            oid=OID_METERS,
            convert=("regex", USAGE_PATTERN),
            unit="m",
            label="print-length",
        ),
    )
