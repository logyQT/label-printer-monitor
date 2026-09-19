"""Sato CL4NX Plus adapter.

Two SNMP requests total:
1. Poke (GET) - check reachability
2. Meters + unit with exponential backoff retry

No label count available via SNMP on Sato.

Declarative specification - see adapters.base for the engine.
"""

from adapters.base import PrinterAdapter, Metric


OID_REACHABILITY = '1.3.6.1.2.1.43.5.1.1.16.1'  # printer name
OID_METERS = '1.3.6.1.2.1.43.10.2.1.4.1.1'     # prtMarkerLifeCount
OID_UNIT = '1.3.6.1.2.1.43.10.2.1.3.1.1'        # prtMarkerCounterUnit

# prtMarkerCounterUnit values → human-readable strings.
# These come from the printer firmware, not user config.
UNIT_MAP = {
    '3': 'sheets',
    '4': 'linearFeet',
    '5': 'linearMeters',
    '17': 'm',  # Sato custom code observed on CL4NX Plus
}


class SatoCL4NXPlusAdapter(PrinterAdapter):

    model_prefixes = ('sato cl4nx plus',)
    snmp_version = 1
    reachability_oid = OID_REACHABILITY
    # meters first: the unit read is independent, but meters is the primary
    # counter and keep the historical fetch order (and test side-effects).
    metrics = (
        Metric('meters_total', oid=OID_METERS, convert='float', label='meters'),
        Metric('meter_unit', oid=OID_UNIT,
               convert=('map', UNIT_MAP, 'unit_code:{}'), label='unit'),
    )