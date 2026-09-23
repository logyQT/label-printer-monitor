"""Zebra ZD621 adapter - EXPERIMENTAL.

The ZD621 is a Link-OS printer, so it is read through the same ZQL "200"
range the ZT411 adapter uses (all three OIDs confirmed on a live ZT411,
see zebra_zt411.py):

- 10642.200.19.7.0 = model name, e.g. "ZD621"       (poke)
- 10642.200.17.2.0 = total label count, STRING "302318"
- 10642.200.17.3.0 = total print length, "555719 INCHES, 1411527 CENTIMETERS"

No ZD621 was available to verify against yet. If the printer stays
unreachable, set ``snmp_version = 0``: pre-Link-OS firmware answers
SNMPv1 only (the GX430t behaves that way).

Declarative specification - see adapters.base for the engine.
"""

from src.adapters.base import Metric, PrinterAdapter

OID_REACHABILITY: str = "1.3.6.1.4.1.10642.200.19.7.0"  # model name (poke)
OID_LABELS: str = "1.3.6.1.4.1.10642.200.17.2.0"  # total label count (STRING)
OID_METERS: str = "1.3.6.1.4.1.10642.200.17.3.0"  # "XX INCHES, XX CENTIMETERS"

# Matches "<number> CENTIMETERS" or "<number> INCHES" in the usage string.
USAGE_PATTERN: str = r"(\d[\d,]*)\s*(CENTIMETERS|INCHES)"


class ZebraZD621Adapter(PrinterAdapter):
    model_prefixes = ("zebra zd621",)
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
