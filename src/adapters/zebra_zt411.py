"""Zebra ZT411 adapter.

Uses vendor OIDs under enterprise 10642.
Labels + meters (cm) confirmed on ZT411.

Declarative specification - see adapters.base for the engine.
"""

from adapters.base import Metric, PrinterAdapter

OID_REACHABILITY: str = "1.3.6.1.4.1.10642.1.1.0"  # model name
OID_LABELS: str = "1.3.6.1.4.1.10642.3.1.6.0"  # labels NONRESET
OID_METERS: str = "1.3.6.1.4.1.10642.3.1.1.0"  # centimeters NONRESET


class ZebraZT411Adapter(PrinterAdapter):
    model_prefixes = ("zebra zt411",)
    snmp_version = 1
    reachability_oid = OID_REACHABILITY
    metrics = (
        Metric("labels_total", oid=OID_LABELS, convert="int", label="labels"),
        Metric("meters_total", oid=OID_METERS, convert="float", unit="cm", label="meters"),
    )
