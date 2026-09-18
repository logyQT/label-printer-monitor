"""Zebra GX430t adapter.

Confirmed OIDs on GX430t (SNMPv1 ONLY):
- 10642.1.1.0 = model name "ZTC GX430t"
- 10642.200.17.7.0 = TOTAL USAGE as STRING: "89667 INCHES, 227775 CENTIMETERS"

Requires SNMPv1 (v2c times out).
"""

import re
from adapters.base import PrinterAdapter


OID_REACHABILITY = '1.3.6.1.4.1.10642.1.1.0'  # model name
OID_TOTAL_USAGE = '1.3.6.1.4.1.10642.200.17.7.0'  # "89667 INCHES, 227775 CENTIMETERS"


class ZebraGX430tAdapter(PrinterAdapter):

    OIDS = {}

    def __init__(self, ip, community='public', timeout_sec=5, retries=2,
                 version=0, **kwargs):
        # Force SNMPv1 — GX430t doesn't respond to v2c
        super().__init__(ip, community, timeout_sec, retries, version=0, **kwargs)

    def get_counters(self) -> dict:
        result = {
            'labels_total': None, 'meters_total': None, 'meter_unit': 'unknown',
            'model_name': '', 'reachable': False,
        }

        model, _ = self._snmp_get(OID_REACHABILITY, label='poke')
        if model is None:
            return result
        result['reachable'] = True
        if isinstance(model, bytes):
            model = model.decode('ascii', errors='replace')
        result['model_name'] = model or ''

        # Total usage: "89667 INCHES, 227775 CENTIMETERS"
        usage_str, _ = self._snmp_get_retry(OID_TOTAL_USAGE, label='usage')
        if usage_str is not None:
            if isinstance(usage_str, bytes):
                usage_str = usage_str.decode('ascii', errors='replace')
            meters = self._parse_usage(usage_str)
            if meters is not None:
                result['meters_total'] = meters
                result['meter_unit'] = 'm'

        return result

    @staticmethod
    def _parse_usage(usage_str):
        """Parse "89667 INCHES, 227775 CENTIMETERS" -> meters."""
        # Try cm first
        match = re.search(r'(\d[\d,]*)\s*CENTIMETERS', usage_str, re.IGNORECASE)
        if match:
            cm = int(match.group(1).replace(',', ''))
            return cm / 100.0  # cm -> m

        # Try inches
        match = re.search(r'(\d[\d,]*)\s*INCHES', usage_str, re.IGNORECASE)
        if match:
            inches = int(match.group(1).replace(',', ''))
            return inches * 0.0254  # in -> m

        return None

    def is_reachable(self) -> bool:
        model, _ = self._snmp_get(OID_REACHABILITY)
        return model is not None
