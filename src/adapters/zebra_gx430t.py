"""Zebra GX430t adapter.

Confirmed OIDs on GX430t (SNMPv1 ONLY):
- 10642.1.1.0 = model name "ZTC GX430t"
- 10642.200.17.7.0 = TOTAL USAGE as STRING: "89667 INCHES, 227775 CENTIMETERS"

Requires SNMPv1 (v2c times out) - declared via snmp_version.

Declarative specification - see adapters.base for the engine.
"""

from adapters.base import Metric, PrinterAdapter

OID_REACHABILITY: str = "1.3.6.1.4.1.10642.1.1.0"  # model name
OID_TOTAL_USAGE: str = "1.3.6.1.4.1.10642.200.17.7.0"  # "89667 INCHES, 227775 CENTIMETERS"

# Matches "<number> CENTIMETERS" or "<number> INCHES" in the usage string.
USAGE_PATTERN: str = r"(\d[\d,]*)\s*(CENTIMETERS|INCHES)"


class ZebraGX430tAdapter(PrinterAdapter):
    model_prefixes = ("zebra gx430t",)
    snmp_version = 0  # GX430t only responds to SNMPv1
    reachability_oid = OID_REACHABILITY
    metrics = (
        Metric(
            "meters_total",
            oid=OID_TOTAL_USAGE,
            convert=("regex", USAGE_PATTERN),
            unit="m",
            label="usage",
        ),
    )
